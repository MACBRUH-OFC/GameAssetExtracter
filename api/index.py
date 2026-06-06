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
import UnityPy

os.environ["UNITYPY_NO_GUI"] = "1"

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    try:
        if data.startswith(b'\x1f\x8b'):
            return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
            return decompress_stream(zlib.decompress(data))
    except:
        pass
    return data

def export_mesh_to_obj(mesh_obj) -> bytes:
    try:
        m = mesh_obj.read()
        out = io.StringIO()
        out.write(f"# Exported from Assets Extractor\no {m.name}\n")
        for v in m.m_Vertices:
            out.write(f"v {v.x} {v.y} {v.z}\n")
        for uv in m.m_UV0:
            out.write(f"vt {uv.x} {uv.y}\n")
        for n in m.m_Normals:
            out.write(f"vn {n.x} {n.y} {n.z}\n")
        for submesh in m.m_SubMeshes:
            indices = m.m_Indices[submesh.firstByte // 2 : (submesh.firstByte // 2) + (submesh.indexCount)]
            for i in range(0, len(indices), 3):
                out.write(f"f {indices[i]+1}/{indices[i]+1}/{indices[i]+1} {indices[i+1]+1}/{indices[i+1]+1}/{indices[i+1]+1} {indices[i+2]+1}/{indices[i+2]+1}/{indices[i+2]+1}\n")
        return out.getvalue().encode('utf-8')
    except:
        return None

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
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']:
            continue
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
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", pristine_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"{safe_name}{ext}", raw, f"TextAssets/{safe_name}{ext}", "TextAsset"

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t

        elif t == "AudioClip":
            samples = getattr(data, "samples", {})
            if samples:
                audio_name = list(samples.keys())[0]
                return audio_name, samples[audio_name], f"Audio/{audio_name}", "AudioClip"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "AudioClip"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1000:
                match = raw_env_data.find(b'ftyp')
                if match != -1: raw = raw_env_data[match-4:match+10_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "VideoClip"

        elif t == "Mesh":
            mesh_data = export_mesh_to_obj(obj)
            if mesh_data:
                return f"{safe_name}.obj", mesh_data, f"Meshes/{safe_name}.obj", "Mesh"
            tree = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode(), f"Meshes/{safe_name}.json", "Mesh (Data)"

        elif t in ["Material", "Shader"]:
            tree = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode(), f"Shaders_Materials/{safe_name}.json", t

        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            ext = ".otf" if raw.startswith(b'OTTO') else ".ttf"
            if len(raw) > 10: return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", "Font"

        tree_data = dump_obj_to_dict(data)
        if tree_data:
            folder = f"Hierarchy/{t}" if t in ["GameObject", "MonoBehaviour"] else f"Other/{t}"
            return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode(), f"{folder}/{safe_name}.json", t

    except:
        pass
    return None

def convert_ktx_astc_to_png(file_bytes) -> bytes:
    try:
        # Simplified KTX/ASTC header parsing for PNG conversion
        if file_bytes.startswith(b'\xABKTX 11\xBB'):
            w = struct.unpack('<I', file_bytes[36:40])[0]
            h = struct.unpack('<I', file_bytes[40:44])[0]
            # Use UnityPy or texture2ddecoder logic here
            # For brevity, this assumes standard conversion path
            pass
    except:
        pass
    return None

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    
    if request.method == 'GET':
        dl_type = request.args.get('download_type', '')
        group_mode = request.args.get('grouped', 'false') == 'true'
        filter_types = request.args.get('filters', '').split(',') if request.args.get('filters') else []

        if dl_type == 'zip':
            if 'extracted' not in GLOBAL_CACHE_REGISTRY: return "No Cache", 400
            zip_io = io.BytesIO()
            with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in GLOBAL_CACHE_REGISTRY['extracted']:
                    if filter_types and item['label'] not in filter_types: continue
                    path = item['zip_path'] if group_mode else item['name']
                    zf.writestr(path, item['bytes'])
            zip_io.seek(0)
            return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=f"{GLOBAL_CACHE_REGISTRY.get('orig_name', 'assets')}[Extracted].zip")

        elif dl_type == 'single':
            idx = int(request.args.get('file_index', -1))
            item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
            return send_file(io.BytesIO(item['bytes']), as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    
    uploaded_file = request.files['asset_bundle']
    raw_name = uploaded_file.filename
    clean_base = re.split(r'[.\-_]', raw_name)[0]
    raw_bytes = uploaded_file.read()
    data = decompress_stream(raw_bytes)
    
    extracted_list = []
    manifest = []
    seen_md5 = set()
    
    try:
        env = UnityPy.load(data)
        for obj in env.objects:
            res = process_object_unrestricted(obj, data)
            if res:
                fname, fbytes, zpath, tlabel = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    extracted_list.append({'name': fname, 'bytes': fbytes, 'zip_path': zpath, 'label': tlabel})
                    manifest.append({'index': len(extracted_list)-1, 'name': fname, 'label': tlabel})
        
        GLOBAL_CACHE_REGISTRY = {'extracted': extracted_list, 'orig_name': clean_base}
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "UI File Missing", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)