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

def export_mesh_to_obj(mesh_data) -> bytes:
    try:
        sb = io.StringIO()
        sb.write(f"# Exported from Unity Assets Extractor\no {mesh_data.name}\n")
        for v in mesh_data.vertices:
            sb.write(f"v {-v.x} {v.y} {v.z}\n")
        for n in mesh_data.normals:
            sb.write(f"vn {-n.x} {n.y} {n.z}\n")
        for uv in mesh_data.uv:
            sb.write(f"vt {uv.x} {uv.y}\n")
        for submesh in mesh_data.submeshes:
            for i in range(0, len(submesh.indices), 3):
                i1, i2, i3 = submesh.indices[i:i+3]
                sb.write(f"f {i3+1}/{i3+1}/{i3+1} {i2+1}/{i2+1}/{i2+1} {i1+1}/{i1+1}/{i1+1}\n")
        return sb.getvalue().encode('utf-8')
    except:
        return b""

def dump_obj_to_dict(obj_data) -> dict:
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'vertices', 'normals', 'uv', 'indices']:
            continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)):
                out[attr] = val
            elif isinstance(val, bytes):
                out[attr] = val.hex()[:200] + "..." if len(val) > 200 else val.hex()
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
            if isinstance(raw, str): raw = raw.encode('utf-8')
            ext = ".txt"
            label = "TextAsset"
            if safe_name.lower().endswith('.atlas') or b"size:" in raw:
                ext = ".atlas"
                label = "Atlas"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "JSON Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", "Texture"
        elif t == "Mesh":
            obj_bytes = export_mesh_to_obj(data)
            if obj_bytes:
                return f"{safe_name}.obj", obj_bytes, f"Meshes/{safe_name}.obj", "3D Mesh"
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_mesh.json", json.dumps(tree_data).encode('utf-8'), f"Meshes/{safe_name}.json", "Mesh Data"
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio"
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    raw = raw_env_data[max(0, match-4):match+20000000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video"
        elif t == "Font":
            raw_font = getattr(data, "m_FontData", b"")
            if len(raw_font) > 10:
                ext = ".otf" if raw_font.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font, f"Fonts/{safe_name}{ext}", "Font"
        elif t in ["Shader", "Material"]:
            tree = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode('utf-8'), f"Shaders_Materials/{safe_name}.json", t
        elif t in ["MonoBehaviour", "GameObject", "AssetBundle"]:
            tree = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode('utf-8'), f"Data/{t}/{safe_name}.json", t
        else:
            try:
                tree = dump_obj_to_dict(data)
                if tree:
                    return f"{safe_name}.json", json.dumps(tree, indent=2).encode('utf-8'), f"Other/{t}/{safe_name}.json", t
            except: pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", t
    except: pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    bytes_of_kv = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + bytes_of_kv)
    data = f.read(struct.unpack('<I', f.read(4))[0])
    if gl_internal_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    else: decoded = data[:width*height*4]
    img = Image.frombytes("RGBA", (width, height), decoded)
    b, g, r, a = img.split()
    img = Image.merge("RGBA", (r, g, b, a)).transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "No data cached"}), 400
        mode = request.args.get('mode', 'normal')
        indices = request.args.get('indices', '')
        filter_indices = [int(i) for i in indices.split(',') if i.strip()] if indices else None
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if filter_indices is not None and i not in filter_indices: continue
                path = item['zip_path'] if mode == 'grouped' else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")
    elif download_type == 'single':
        idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or idx < 0 or idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Invalid index"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])
    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file"}), 400
    try:
        raw_bytes = request.files['asset_bundle'].read()
        decomp = decompress_stream(raw_bytes)
        ext_list = []
        manifest = []
        if decomp.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            png = convert_ktx_to_png_fallback(decomp)
            ext_list.append({'name': 'texture.png', 'zip_path': 'Textures/texture.png', 'bytes': png})
            manifest.append({'index': 0, 'name': 'texture.png', 'path': 'Textures/texture.png', 'label': 'Texture'})
        else:
            env = UnityPy.load(decomp)
            seen_md5 = set()
            counter = 0
            for obj in env.objects:
                res = process_object_unrestricted(obj, decomp)
                if res:
                    fname, fbytes, zpath, flabel = res
                    h = hashlib.md5(fbytes).hexdigest()
                    if h not in seen_md5:
                        seen_md5.add(h)
                        ext_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                        manifest.append({'index': counter, 'name': fname, 'path': zpath, 'label': flabel})
                        counter += 1
            del env
            gc.collect()
        if not ext_list: return jsonify({"error": "No assets found"}), 400
        GLOBAL_CACHE_REGISTRY['extracted'] = ext_list
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)