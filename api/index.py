import os
import io
import json
import gzip
import zlib
import zipfile
import re
import gc
import hashlib
import struct
from flask import Flask, request, send_file, jsonify
from PIL import Image
import texture2ddecoder

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def extract_original_asset_name(obj, data, default_type: str) -> str:
    """Extracts the pure original internal name from the asset maps without modifications."""
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"{default_type}_{obj.path_id}"

def build_wavefront_obj_file(mesh_data) -> bytes:
    """Parses Unity Mesh channels directly into standard Wavefront 3D .obj asset strings."""
    try:
        out = io.StringIO()
        out.write(f"# Extracted Wavefront OBJ - Mesh: {getattr(mesh_data, 'name', 'Mesh')}\n")
        
        # Pull geometric channel maps safely from the engine reader
        if hasattr(mesh_data, "m_Vertices") and mesh_data.m_Vertices:
            verts = mesh_data.m_Vertices
            for i in range(0, len(verts), 3):
                if i+2 < len(verts):
                    out.write(f"v {verts[i]} {verts[i+1]} {verts[i+2]}\n")
                    
        if hasattr(mesh_data, "m_Normals") and mesh_data.m_Normals:
            norms = mesh_data.m_Normals
            for i in range(0, len(norms), 3):
                if i+2 < len(norms):
                    out.write(f"vn {norms[i]} {norms[i+1]} {norms[i+2]}\n")

        if hasattr(mesh_data, "m_UV0") and mesh_data.m_UV0:
            uvs = mesh_data.m_UV0
            for i in range(0, len(uvs), 2):
                if i+1 < len(uvs):
                    out.write(f"vt {uvs[i]} {uvs[i+1]}\n")

        # Reconstruct face indices vectors smoothly
        if hasattr(mesh_data, "m_Indices") and mesh_data.m_Indices:
            indices = mesh_data.m_Indices
            for i in range(0, len(indices), 3):
                if i+2 < len(indices):
                    # OBJ index structure counts from 1 base offset
                    v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                    out.write(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}\n")
                    
        return out.getvalue().encode('utf-8')
    except:
        return b""

def dump_obj_to_dict(obj_data) -> dict:
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"): return obj_data.read_typetree()
    except: pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']: continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)): out[attr] = val
        except: pass
    return out

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_original_asset_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        # 3D Objects / Mesh Extraction Matrix Route
        if t == "Mesh":
            obj_mesh_bytes = build_wavefront_obj_file(data)
            if obj_mesh_bytes and len(obj_mesh_bytes) > 50:
                return f"{safe_name}.obj", obj_mesh_bytes, f"Geometry/{safe_name}.obj", "3D Mesh Object", pristine_name

        # Text Configurations Assets Handling
        elif t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".txt"
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or b"size:" in raw:
                if not safe_name.lower().endswith('.atlas'): ext = ".atlas.txt"
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label, pristine_name

        # Textures and Sprite Systems
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            img_bytes = buf.getvalue()
            buf.close()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", f"{t} Asset", pristine_name

        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_atlas_map.json", js_bytes, f"Mapping/{safe_name}_atlas_map.json", "SpriteAtlas Map", pristine_name

        # Audio Track Framework Core Handling
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track", pristine_name
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track", pristine_name

        # Video Streaming Clips Engine
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip", pristine_name

        # Structural Schemas / Hierarchies Fallbacks
        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject", "Material", "Shader", "SkinnedMeshRenderer", "AnimationClip", "AnimatorController", "Animator", "AssetBundle"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Structures/{t}/{safe_name}.json", f"{t} Layout", pristine_name

        # Typography Data Arrays Mapping
        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".ttf"
                if raw_font_data.startswith(b'OTTO'): ext = ".otf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font File", pristine_name

        # Unified Structured Catch-All Extraction Routines
        tree_data = dump_obj_to_dict(data)
        if tree_data:
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Other_Metadata/{t}/{safe_name}.json", f"{t} Node Data", pristine_name
    except: pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n': raise Exception("Invalid KTX format.")
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width, height = struct.unpack('<I', header[36:40])[0], struct.unpack('<I', header[40:44])[0]
    f.seek(64 + struct.unpack('<I', header[60:64])[0])
    data = f.read(struct.unpack('<I', f.read(4))[0])

    if gl_internal_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8)}
        bx, by = astc_formats.get(gl_internal_format, (4,4))
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    else: decoded = data[:width*height*4]

    img = Image.frombytes("RGBA", (width, height), decoded)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a)).transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'): return jsonify({"error": "Cache empty."}), 400
        
        target_indices = request.args.get('indices', '')
        group_by_name = request.args.get('group_mapped', 'false') == 'true'
        
        allowed_indices = []
        if target_indices:
            allowed_indices = [int(x) for x in target_indices.split(',') if x.strip()]

        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                if allowed_indices and item['index'] not in allowed_indices: continue
                
                # Dynamic file architecture layout mapping routine
                if group_by_name:
                    clean_folder_ref = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item['orig_name'])
                    final_path = f"Grouped_Assets/{clean_folder_ref}/{item['name']}"
                else:
                    final_path = item['zip_path']
                    
                zf.writestr(final_path, item['bytes'])
                
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_package.zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Out of bounds."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files: return jsonify({"error": "Missing file file stream."}), 400

    try:
        uploaded_file = request.files['asset_bundle']
        orig_name = os.path.basename(uploaded_file.filename)
        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)

        extracted_list, json_metadata_manifest = [], []
        tracking_index_counter = 0

        if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            try:
                png_bytes = convert_ktx_to_png_fallback(decompressed_data)
                clean_title = os.path.splitext(orig_name)[0]
                extracted_list.append({'index': 0, 'name': f"{clean_title}.png", 'zip_path': f"Textures/{clean_title}.png", 'bytes': png_bytes, 'orig_name': clean_title})
                json_metadata_manifest.append({'index': 0, 'name': f"{clean_title}.png", 'path': f"Textures/{clean_title}.png", 'label': "KTX Image"})
                GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
                return jsonify({"files": json_metadata_manifest})
            except: pass

        try:
            env = UnityPy.load(decompressed_data)
            objects_array = env.objects
        except:
            try:
                png_bytes = convert_ktx_to_png_fallback(decompressed_data)
                clean_title = os.path.splitext(orig_name)[0]
                extracted_list.append({'index': 0, 'name': f"{clean_title}.png", 'zip_path': f"Textures/{clean_title}.png", 'bytes': png_bytes, 'orig_name': clean_title})
                json_metadata_manifest.append({'index': 0, 'name': f"{clean_title}.png", 'path': f"Textures/{clean_title}.png", 'label': "KTX Image"})
                GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
                return jsonify({"files": json_metadata_manifest})
            except: return jsonify({"error": "Failed parsing archive layers."}), 400

        seen_md5 = set()
        for obj in objects_array:
            res = process_object_unrestricted(obj, decompressed_data)
            if res:
                filename, file_bytes, zip_path, type_label, original_raw_name = res
                h = hashlib.md5(file_bytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    extracted_list.append({
                        'index': tracking_index_counter, 'name': filename, 
                        'zip_path': zip_path, 'bytes': file_bytes, 'orig_name': original_raw_name
                    })
                    json_metadata_manifest.append({
                        'index': tracking_index_counter, 'name': filename, 
                        'path': zip_path, 'label': type_label
                    })
                    tracking_index_counter += 1
        del env
        gc.collect()

        if tracking_index_counter == 0: return jsonify({"error": "No elements found inside asset mappings."}), 400
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
    except Exception as e:
        return jsonify({"error": f"Process crash log: {str(e)}"}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: return f"Missing layout index maps: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)