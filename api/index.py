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
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    out = {}
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']: continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)): out[attr] = val
        except: pass
    return out

def export_unity_mesh_to_obj(data) -> str:
    """Manually reads and reconstructs raw Unity geometry data into Wavefront OBJ format."""
    obj_lines = []
    try:
        name = getattr(data, "m_Name", "Mesh")
        obj_lines.append(f"# Unity Mesh Extractor Export: {name}\n")
        
        # Pull geometric vertex stream records if accessible
        if hasattr(data, "m_Vertices") and data.m_Vertices:
            for v in data.m_Vertices:
                obj_lines.append(f"v {v.x} {v.y} {v.z}\n")
        elif hasattr(data, "vertices") and data.vertices:
            for v in data.vertices:
                obj_lines.append(f"v {v.x} {v.y} {v.z}\n")

        # Pull visual surface layout mappings (UV Texture Maps)
        if hasattr(data, "m_UV0") and data.m_UV0:
            for uv in data.m_UV0:
                obj_lines.append(f"vt {uv.x} {uv.y}\n")

        # Pull vertex normal vector alignments
        if hasattr(data, "m_Normals") and data.m_Normals:
            for n in data.m_Normals:
                obj_lines.append(f"vn {n.x} {n.y} {n.z}\n")

        # Reconstruct face matrix indexes
        if hasattr(data, "m_Indices") and data.m_Indices:
            indices = data.m_Indices
            for i in range(0, len(indices), 3):
                if i+2 < len(indices):
                    v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                    obj_lines.append(f"f {v1} {v2} {v3}\n")
        elif hasattr(data, "indices") and data.indices:
            indices = data.indices
            for i in range(0, len(indices), 3):
                if i+2 < len(indices):
                    v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                    obj_lines.append(f"f {v1} {v2} {v3}\n")
                    
        return "".join(obj_lines)
    except Exception as e:
        return f"# Failed to parse 3D data stream: {str(e)}"

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        # Build cross-linking lookup metadata maps
        meta_deps = {
            "path_id": obj.path_id,
            "mesh_ref": str(getattr(data, "m_Mesh", "")),
            "material_refs": [str(m) for m in getattr(data, "m_Materials", [])] if hasattr(data, "m_Materials") else []
        }

        # 1. Standard Text Configs
        if t == "TextAsset":
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
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label, meta_deps

        # 2. Textures & Graphical Sprites
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            img_bytes = buf.getvalue()
            buf.close()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", f"{t} Asset", meta_deps

        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_atlas_map.json", js_bytes, f"Mapping/{safe_name}_atlas_map.json", "SpriteAtlas Map", meta_deps

        # 3. Audio Tracks
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track", meta_deps
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track", meta_deps

        # 4. Video Clips
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip", meta_deps

        # 5. Advanced 3D Engine Objects (Mesh Framework Reconstruction)
        elif t in ["Mesh", "MeshFilter"]:
            obj_content = export_unity_mesh_to_obj(data)
            if len(obj_content) > 100:
                return f"{safe_name}.obj", obj_content.encode('utf-8'), f"Geometry/{safe_name}.obj", "3D Model Mesh", meta_deps
            else:
                tree_data = dump_obj_to_dict(data)
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_mesh.json", js_bytes, f"Geometry/Meta/{safe_name}.json", "Mesh Structural Layout", meta_deps

        # 6. Scene Nodes, Structural Models, and Behaviors
        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject", "SkinnedMeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Hierarchy/{t}/{safe_name}.json", f"{t} Schema", meta_deps

        # 7. Rendering Systems (Shaders/Materials)
        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Shaders_Materials/{t}/{safe_name}.json", f"{t} Config", meta_deps

        # 8. Motion Timelines
        elif t in ["AnimationClip", "AnimatorController", "Animator"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Animations/{t}/{safe_name}.json", "Animation Timeline Map", meta_deps

        # 9. Font Engine Sets
        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".ttf"
                if raw_font_data.startswith(b'OTTO'): ext = ".otf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "TrueType Font File", meta_deps
            else:
                tree_data = dump_obj_to_dict(data)
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_font_meta.json", js_bytes, f"Fonts/{safe_name}_font_meta.json", "Font Metadata", meta_deps

        # 10. Core Packages
        elif t == "AssetBundle":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_manifest.json", js_bytes, f"Containers/{safe_name}_manifest.json", "Bundle Manifest Container", meta_deps

        # 11. Generic Global Fallback System Block
        else:
            tree_data = dump_obj_to_dict(data)
            if tree_data:
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_{t}.json", js_bytes, f"Other/{t}/{safe_name}.json", f"Raw Data Container ({t})", meta_deps
            raw_bytes = obj.get_raw_data()
            if raw_bytes and len(raw_bytes) > 0:
                return f"{safe_name}_{t}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", f"Binary Block Object ({t})", meta_deps
    except: pass
    return None

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    group_related = request.args.get('group_related', 'false') == 'true'

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache is unpopulated."}), 400
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                target_path = item['zip_path']
                
                # Apply dependency structural grouping if active
                if group_related:
                    clean_name = os.path.splitext(item['name'])[0]
                    target_path = f"Grouped_Assets/{clean_name}/{item['name']}"
                    
                zf.writestr(target_path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Index reference out of bounds."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        
        ext = item['name'].split('.')[-1].lower()
        mimetype = 'application/octet-stream'
        if ext in ['png', 'jpg', 'webp']: mimetype = 'image/png'
        elif ext in ['mp3', 'wav', 'ogg']: mimetype = 'audio/mpeg'
        elif ext in ['json', 'txt', 'obj']: mimetype = 'text/plain; charset=utf-8'
        
        return send_file(io.BytesIO(item['bytes']), mimetype=mimetype, as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file upload source found."}), 400

    try:
        uploaded_file = request.files['asset_bundle']
        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)

        extracted_list = []
        json_metadata_manifest = []
        tracking_index_counter = 0

        try:
            env = UnityPy.load(decompressed_data)
            objects_array = env.objects
        except:
            return jsonify({"error": "Unrecognized package file blocks."}), 400

        seen_md5 = set()
        for obj in objects_array:
            res = process_object_unrestricted(obj, decompressed_data)
            if res:
                filename, file_bytes, zip_folder_path, type_label, meta = res
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
            return jsonify({"error": "No deployable assets matched."}), 400

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
    except Exception as e:
        return jsonify({"error": f"Internal process error: {str(e)}"}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: return f"Source file missing: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)