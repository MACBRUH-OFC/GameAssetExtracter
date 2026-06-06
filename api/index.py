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

def mesh_to_obj(mesh_data) -> str:
    try:
        sb = []
        sb.append(f"g {mesh_data.name}")
        for v in mesh_data.m_Vertices:
            sb.append(f"v {v.x} {v.y} {v.z}")
        for u in mesh_data.m_UV0:
            sb.append(f"vt {u.x} {u.y}")
        for n in mesh_data.m_Normals:
            sb.append(f"vn {n.x} {n.y} {n.z}")
        for sub in mesh_data.m_SubMeshes:
            i = sub.firstByte // 2
            for _ in range(sub.indexCount // 3):
                idx1 = mesh_data.m_Indices[i] + 1
                idx2 = mesh_data.m_Indices[i+1] + 1
                idx3 = mesh_data.m_Indices[i+2] + 1
                sb.append(f"f {idx1}/{idx1}/{idx1} {idx2}/{idx2}/{idx2} {idx3}/{idx3}/{idx3}")
                i += 3
        return "\n".join(sb)
    except:
        return ""

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
            label = "TextAsset"
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
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_atlas.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Mapping/{safe_name}.json", t
        elif t == "AudioClip":
            samples = getattr(data, "samples", {})
            if samples:
                name = list(samples.keys())[0]
                return name, samples[name], f"Audio/{name}", t
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", t
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1000:
                match = raw_env_data.find(b'ftyp')
                if match != -1: raw = raw_env_data[match-4:match+15000000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", t
        elif t == "Mesh":
            obj_str = mesh_to_obj(data)
            if obj_str:
                return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", t
            return f"{safe_name}.json", json.dumps(dump_obj_to_dict(data)).encode('utf-8'), f"Meshes/{safe_name}.json", t
        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject", "Material", "Shader", "AnimationClip", "AnimatorController", "Animator", "SkinnedMeshRenderer", "MeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            folder = "Hierarchy" if t in ["GameObject", "MonoBehaviour"] else "Logic"
            if t in ["Material", "Shader"]: folder = "Shaders"
            if "Anim" in t: folder = "Animations"
            if "Mesh" in t: folder = "Geometry"
            return f"{safe_name}_{t}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"{folder}/{t}/{safe_name}.json", t
        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            ext = ".ttf" if not raw.startswith(b'OTTO') else ".otf"
            if len(raw) > 10: return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", t
            return f"{safe_name}_font.json", json.dumps(dump_obj_to_dict(data)).encode('utf-8'), f"Fonts/{safe_name}.json", t
        elif t == "AssetBundle":
            return f"{safe_name}_manifest.json", json.dumps(dump_obj_to_dict(data)).encode('utf-8'), f"Bundles/{safe_name}.json", t
        else:
            try:
                tree = dump_obj_to_dict(data)
                if tree: return f"{safe_name}_{t}.json", json.dumps(tree).encode('utf-8'), f"Other/{t}/{safe_name}.json", t
            except: pass
            raw = obj.get_raw_data()
            if raw: return f"{safe_name}_{t}.dat", raw, f"Other/{t}/{safe_name}.dat", t
    except: pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    gl_format = struct.unpack('<I', header[28:32])[0]
    w = struct.unpack('<I', header[36:40])[0]
    h = struct.unpack('<I', header[40:44])[0]
    kv = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + kv)
    sz = struct.unpack('<I', f.read(4))[0]
    data = f.read(sz)
    if gl_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, w, h)
    elif 0x93B0 <= gl_format <= 0x93BD:
        formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = formats[gl_format]
        decoded = texture2ddecoder.decode_astc(data, w, h, bx, by)
    else: decoded = data
    img = Image.frombytes("RGBA", (w, h), decoded)
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    if download_type == 'zip':
        mode = request.args.get('mode', 'normal')
        indices = request.args.get('indices', '')
        indices_list = [int(i) for i in indices.split(',') if i] if indices else []
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache is empty."}), 400
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if indices_list and idx not in indices_list: continue
                path = item['zip_path'] if mode == 'grouped' else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")
    elif download_type == 'single':
        idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or idx < 0 or idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Invalid index."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
        return send_file(io.BytesIO(item['bytes']), as_attachment=True, download_name=item['name'])
    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    try:
        up_file = request.files['asset_bundle']
        raw = up_file.read()
        data = decompress_stream(raw)
        extracted = []
        manifest = []
        if data.startswith(b'\xABKTX 11\xBB'):
            try:
                png = convert_ktx_to_png_fallback(data)
                name = os.path.splitext(up_file.filename)[0] + ".png"
                extracted.append({'name': name, 'zip_path': f"Textures/{name}", 'bytes': png})
                manifest.append({'index': 0, 'name': name, 'label': "Texture2D"})
                GLOBAL_CACHE_REGISTRY['extracted'] = extracted
                return jsonify({"files": manifest})
            except: pass
        try:
            env = UnityPy.load(data)
            objs = env.objects
        except:
            return jsonify({"error": "Format not supported."}), 400
        seen = set()
        count = 0
        for obj in objs:
            res = process_object_unrestricted(obj, data)
            if res:
                fname, fbytes, zpath, tlabel = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen:
                    seen.add(h)
                    extracted.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes, 'type': tlabel})
                    manifest.append({'index': count, 'name': fname, 'label': tlabel})
                    count += 1
        del env
        gc.collect()
        if not extracted: return jsonify({"error": "No assets found."}), 400
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except: return "UI Missing", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)