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
        if data.startswith(b'\x1f\x8b'):
            return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
            return decompress_stream(zlib.decompress(data))
    except:
        pass
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
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'mesh_data']:
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
            ext = ".txt"
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or b"size:" in raw:
                ext = ".atlas"
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", f"{t} Asset"

        elif t == "Mesh":
            try:
                mesh_str = data.export()
                if isinstance(mesh_str, str):
                    return f"{safe_name}.obj", mesh_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "3D Mesh"
            except:
                pass
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_mesh.json", json.dumps(tree_data).encode(), f"Meshes/{safe_name}.json", "Mesh Data"

        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1000:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    raw = raw_env_data[match-4:match+15000000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip"

        elif t in ["MonoBehaviour", "ScriptableObject", "GameObject"]:
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_{t}.json", json.dumps(tree_data, indent=2).encode(), f"Hierarchy/{t}/{safe_name}.json", f"{t} Data"

        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode(), f"Shaders_Materials/{t}/{safe_name}.json", f"{t} Config"

        elif t == "Font":
            raw_font = getattr(data, "m_FontData", b"")
            ext = ".ttf" if not raw_font.startswith(b'OTTO') else ".otf"
            if len(raw_font) > 10:
                return f"{safe_name}{ext}", raw_font, f"Fonts/{safe_name}{ext}", "Font File"

        tree_data = dump_obj_to_dict(data)
        if tree_data:
            return f"{safe_name}_{t}.json", json.dumps(tree_data, indent=2).encode(), f"Other/{t}/{safe_name}.json", f"Raw {t}"
    except:
        pass
    return None

def decode_astc_to_png(data: bytes) -> bytes:
    if not data.startswith(b'\x13\xAB\xA1\x5C'):
        raise Exception("Invalid ASTC Header")
    block_width = data[4]
    block_height = data[5]
    width = data[7] | (data[8] << 8) | (data[9] << 16)
    height = data[10] | (data[11] << 8) | (data[12] << 16)
    actual_data = data[16:]
    decoded = texture2ddecoder.decode_astc(actual_data, width, height, block_width, block_height)
    img = Image.frombytes("RGBA", (width, height), decoded)
    buf = io.BytesIO()
    img.transpose(Image.FLIP_TOP_BOTTOM).save(buf, format="PNG")
    return buf.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_extraction():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    
    if download_type == 'zip':
        mode = request.args.get('mode', 'normal') # normal, grouped, filtered
        filters = request.args.get('filters', '').split(',') if request.args.get('filters') else []
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache empty"}), 400
        
        zip_io = io.BytesIO()
        orig_filename = GLOBAL_CACHE_REGISTRY.get('orig_name', 'assets')
        clean_zip_name = re.split(r'[.\-_]', orig_filename)[0]
        
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                if mode == 'filtered' and filters and item['label'] not in filters:
                    continue
                path = item['zip_path'] if mode == 'grouped' else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=f"{clean_zip_name}[Extracted].zip")

    if download_type == 'single':
        idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or idx < 0 or idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Invalid index"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
        return send_file(io.BytesIO(item['bytes']), as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file"}), 400

    f = request.files['asset_bundle']
    raw_data = f.read()
    GLOBAL_CACHE_REGISTRY['orig_name'] = f.filename
    decompressed = decompress_stream(raw_data)
    
    extracted_list = []
    manifest = []
    
    if decompressed.startswith(b'\x13\xAB\xA1\x5C'):
        try:
            png = decode_astc_to_png(decompressed)
            name = os.path.splitext(f.filename)[0] + ".png"
            extracted_list.append({'name': name, 'zip_path': f"Textures/{name}", 'bytes': png, 'label': "ASTC Texture"})
            manifest.append({'index': 0, 'name': name, 'path': f"Textures/{name}", 'label': "ASTC Texture"})
            GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
            return jsonify({"files": manifest})
        except: pass

    try:
        env = UnityPy.load(decompressed)
        seen = set()
        count = 0
        for obj in env.objects:
            res = process_object_unrestricted(obj, decompressed)
            if res:
                fname, fbytes, zpath, flabel = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen:
                    seen.add(h)
                    extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes, 'label': flabel})
                    manifest.append({'index': count, 'name': fname, 'path': zpath, 'label': flabel})
                    count += 1
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_index(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "UI Source Missing", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)