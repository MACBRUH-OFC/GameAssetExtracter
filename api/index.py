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
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'm_Script']:
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

def export_mesh_obj(data) -> bytes:
    try:
        sb = []
        sb.append(f"# Exported from Assets Extractor\no {data.name}")
        if hasattr(data, "m_Vertices") and data.m_Vertices:
            v = data.m_Vertices
            for i in range(0, len(v), 3):
                sb.append(f"v {-v[i]} {v[i+1]} {v[i+2]}")
        if hasattr(data, "m_Normals") and data.m_Normals:
            n = data.m_Normals
            for i in range(0, len(n), 3):
                sb.append(f"vn {-n[i]} {n[i+1]} {n[i+2]}")
        if hasattr(data, "m_UV0") and data.m_UV0:
            u = data.m_UV0
            for i in range(0, len(u), 2):
                sb.append(f"vt {u[i]} {u[i+1]}")
        if hasattr(data, "m_Indices") and data.m_Indices:
            idx = data.m_Indices
            for i in range(0, len(idx), 3):
                v1, v2, v3 = idx[i]+1, idx[i+1]+1, idx[i+2]+1
                sb.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
        return "\n".join(sb).encode('utf-8')
    except:
        return b""

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
                ext = ".atlas.txt"; label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"; label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Texts/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", f"{t} Asset"

        elif t == "Mesh":
            obj_bytes = export_mesh_obj(data)
            if obj_bytes:
                return f"{safe_name}.obj", obj_bytes, f"Meshes/{safe_name}.obj", "3D Mesh"
            tree = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree).encode(), f"Meshes/{safe_name}.json", "Mesh Data"

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

        elif t in ["Shader", "Material"]:
            tree = dump_obj_to_dict(data)
            js = json.dumps(tree, indent=2).encode()
            return f"{safe_name}.json", js, f"Shaders_Materials/{safe_name}.json", f"{t} Config"

        elif t == "Font":
            raw_font = getattr(data, "m_FontData", b"")
            if len(raw_font) > 10:
                ext = ".otf" if raw_font.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font, f"Fonts/{safe_name}{ext}", "Font File"
            return f"{safe_name}_meta.json", json.dumps(dump_obj_to_dict(data)).encode(), f"Fonts/{safe_name}.json", "Font Meta"

        elif t in ["MonoBehaviour", "GameObject", "AssetBundle"]:
            tree = dump_obj_to_dict(data)
            js = json.dumps(tree, indent=2).encode()
            return f"{safe_name}.json", js, f"Metadata/{t}/{safe_name}.json", f"{t} Data"

        else:
            tree = dump_obj_to_dict(data)
            if tree:
                return f"{safe_name}.json", json.dumps(tree).encode(), f"Other/{t}/{safe_name}.json", t
            raw = obj.get_raw_data()
            if raw:
                return f"{safe_name}.dat", raw, f"Other/{t}/{safe_name}.dat", f"Binary {t}"
    except:
        pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    f.seek(64 + struct.unpack('<I', header[60:64])[0])
    data = f.read(struct.unpack('<I', f.read(4))[0])
    if gl_internal_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD: decoded = texture2ddecoder.decode_astc(data, width, height, 4, 4)
    else: decoded = data
    img = Image.frombytes("RGBA", (width, height), decoded)
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    
    if download_type == 'zip':
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: return jsonify({"error": "No cache"}), 400
        is_grouped = request.args.get('grouped', 'false') == 'true'
        indices_str = request.args.get('indices', '')
        filter_indices = [int(x) for x in indices_str.split(',') if x.strip()] if indices_str else None
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if filter_indices is not None and idx not in filter_indices: continue
                path = item['zip_path'] if is_grouped else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    if download_type == 'single':
        idx = int(request.args.get('file_index', -1))
        if idx < 0 or idx >= len(GLOBAL_CACHE_REGISTRY.get('extracted', [])): return jsonify({"error": "OOB"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
        return send_file(io.BytesIO(item['bytes']), as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    
    try:
        uploaded_file = request.files['asset_bundle']
        raw_bytes = uploaded_file.read()
        decompressed = decompress_stream(raw_bytes)
        
        extracted_list = []
        manifest = []
        seen_md5 = set()

        if decompressed.startswith(b'\xABKTX 11'):
            png = convert_ktx_to_png_fallback(decompressed)
            name = os.path.splitext(uploaded_file.filename)[0] + ".png"
            extracted_list.append({'name': name, 'zip_path': f"Textures/{name}", 'bytes': png})
            manifest.append({'index': 0, 'name': name, 'path': f"Textures/{name}", 'label': "KTX Image"})
        else:
            env = UnityPy.load(decompressed)
            count = 0
            for obj in env.objects:
                res = process_object_unrestricted(obj, decompressed)
                if res:
                    fname, fbytes, zpath, tlabel = res
                    h = hashlib.md5(fbytes).hexdigest()
                    if h not in seen_md5:
                        seen_md5.add(h)
                        extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                        manifest.append({'index': count, 'name': fname, 'path': zpath, 'label': tlabel})
                        count += 1
            del env
            gc.collect()

        if not manifest: return jsonify({"error": "Nothing found"}), 400
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)