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

def extract_clean_name(obj, data, default_type: str) -> str:
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"{default_type}_{obj.path_id}"

def dump_obj_to_dict(obj_data) -> dict:
    """Helper method to robustly pull serialized type tree properties safely into dictionaries."""
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']: continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)):
                out[attr] = val
            elif isinstance(val, bytes):
                out[attr] = val.hex()[:500] + "..." if len(val) > 500 else val.hex()
        except:
            pass
    return out

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        # 1 & 2. Standard Text and Specialized Config Mappings
        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".txt"
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or raw.startswith(b"\n") or b"size:" in raw:
                if not safe_name.lower().endswith('.atlas'): ext = ".atlas.txt"
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        # 3 & 4. Textures, Sprite Elements & SpriteAtlas Packaging
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            img_bytes = buf.getvalue()
            buf.close()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", f"{t} Asset"

        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_atlas_map.json", js_bytes, f"Mapping/{safe_name}_atlas_map.json", "SpriteAtlas Map"

        # 5. Audio Processing Structures
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track"

        # 6. Video Component Substructures
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip"

        # 7, 8, & 9. Scene Hierarchy / Nodes (GameObject, MonoBehaviour, ScriptableObject)
        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Hierarchy/{t}/{safe_name}.json", f"{t} Schema"

        # 10 & 11. Visual Engine Renderers (Material, Shader)
        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Shaders_Materials/{t}/{safe_name}.json", f"{t} Config"

        # 12 & 13. Engine Geometric Structural Frameworks (Mesh, SkinnedMeshRenderer)
        elif t in ["Mesh", "SkinnedMeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Geometry/{t}/{safe_name}.json", f"Mesh Structural Layout"

        # 14, 15, & 16. Structural System Movements (AnimationClip, AnimatorController, Animator)
        elif t in ["AnimationClip", "AnimatorController", "Animator"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Animations/{t}/{safe_name}.json", "Animation Timeline Map"

        # 17. Typography Engine Packages (Font)
        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".ttf"
                if raw_font_data.startswith(b'OTTO'): ext = ".otf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "TrueType Font File"
            else:
                tree_data = dump_obj_to_dict(data)
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_font_meta.json", js_bytes, f"Fonts/{safe_name}_font_meta.json", "Font Metadata"

        # 18. Main Containers (AssetBundle)
        elif t == "AssetBundle":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_manifest.json", js_bytes, f"Containers/{safe_name}_manifest.json", "Bundle Manifest Container"

        # 19+. Global Catch-All Fallback Routine for Any Other Unlisted Types
        else:
            try:
                tree_data = dump_obj_to_dict(data)
                if tree_data:
                    js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                    return f"{safe_name}_{t}.json", js_bytes, f"Other/{t}/{safe_name}.json", f"Raw Data Container ({t})"
            except:
                pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes and len(raw_bytes) > 0:
                return f"{safe_name}_{t}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", f"Binary Block Object ({t})"
    except:
        pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n':
        raise Exception("Not a valid KTX format structure.")

    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    bytes_of_kv = struct.unpack('<I', header[60:64])[0]

    f.seek(64 + bytes_of_kv)
    image_size = struct.unpack('<I', f.read(4))[0]
    data = f.read(image_size)

    if gl_internal_format == 0x8D64:
        decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {
            0x93B0: (4, 4), 0x93B1: (5, 4), 0x93B2: (5, 5), 0x93B3: (6, 5),
            0x93B4: (6, 6), 0x93B5: (8, 5), 0x93B6: (8, 6), 0x93B7: (8, 8),
            0x93B8: (10, 5), 0x93B9: (10, 6), 0x93BA: (10, 8), 0x93BB: (10, 10),
            0x93BC: (12, 10), 0x93BD: (12, 12)
        }
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    elif gl_internal_format == 0x8058:
        expected = width * height * 4
        if len(data) < expected: raise Exception("Truncated texture buffer array maps.")
        decoded = data[:expected]
    else:
        raise Exception(f"Unsupported encoding structure format: {hex(gl_internal_format)}")

    img = Image.frombytes("RGBA", (width, height), decoded)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a))
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache is currently unpopulated."}), 400
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Target node out of bounds data index reference."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        
        ext = item['name'].split('.')[-1].lower()
        mimetype = 'application/octet-stream'
        if ext in ['png', 'jpg', 'jpeg', 'webp']: mimetype = 'image/png'
        elif ext in ['mp3', 'wav', 'ogg']: mimetype = 'audio/mpeg'
        elif ext in ['json', 'txt', 'xml', 'atlas']: mimetype = 'text/plain; charset=utf-8'
        elif ext in ['ttf', 'otf']: mimetype = 'font/ttf'
        
        return send_file(io.BytesIO(item['bytes']), mimetype=mimetype, as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file upload source found."}), 400

    try:
        uploaded_file = request.files['asset_bundle']
        orig_name = os.path.basename(uploaded_file.filename)
        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)

        extracted_list = []
        json_metadata_manifest = []
        tracking_index_counter = 0

        if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            try:
                png_bytes = convert_ktx_to_png_fallback(decompressed_data)
                clean_base_title = os.path.splitext(orig_name)[0]
                extracted_list.append({'name': f"{clean_base_title}.png", 'zip_path': f"Textures/{clean_base_title}.png", 'bytes': png_bytes})
                json_metadata_manifest.append({'index': 0, 'name': f"{clean_base_title}.png", 'path': f"Textures/{clean_base_title}.png", 'label': "KTX Image"})
                GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
                return jsonify({"files": json_metadata_manifest})
            except: pass

        try:
            env = UnityPy.load(decompressed_data)
            objects_array = env.objects
        except:
            try:
                png_bytes = convert_ktx_to_png_fallback(decompressed_data)
                clean_base_title = os.path.splitext(orig_name)[0]
                extracted_list.append({'name': f"{clean_base_title}.png", 'zip_path': f"Textures/{clean_base_title}.png", 'bytes': png_bytes})
                json_metadata_manifest.append({'index': 0, 'name': f"{clean_base_title}.png", 'path': f"Textures/{clean_base_title}.png", 'label': "KTX Image"})
                GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
                return jsonify({"files": json_metadata_manifest})
            except:
                return jsonify({"error": "Unrecognized package: Unpacking execution routines failed."}), 400

        seen_md5 = set()
        for obj in objects_array:
            res = process_object_unrestricted(obj, decompressed_data)
            if res:
                filename, file_bytes, zip_folder_path, type_label = res
                h = hashlib.md5(file_bytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    extracted_list.append({'name': filename, 'zip_path': zip_folder_path, 'bytes': file_bytes})
                    json_metadata_manifest.append({
                        'index': tracking_index_counter, 
                        'name': filename, 
                        'path': zip_folder_path,
                        'label': type_label
                    })
                    tracking_index_counter += 1
        del env
        gc.collect()

        if tracking_index_counter == 0:
            return jsonify({"error": "No elements matched within the file blocks."}), 400

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
    except Exception as e:
        return jsonify({"error": f"Internal process error: {str(e)}"}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Source asset mapping system missing: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)