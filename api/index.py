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
    except: pass
    return data

def extract_clean_name(obj, data, default_type: str) -> str:
    if hasattr(obj, 'container') and obj.container:
        base = os.path.basename(obj.container)
        if base: return os.path.splitext(base)[0]
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip(): return val.strip()
    return f"{default_type}_{obj.path_id}"

def export_mesh_to_obj(mesh) -> str:
    try:
        sb = []
        sb.append(f"# Exported from UnityPy\ng {mesh.name}")
        for v in mesh.m_Vertices:
            sb.append(f"v {v.x} {v.y} {v.z}")
        for n in mesh.m_Normals:
            sb.append(f"vn {n.x} {n.y} {n.z}")
        for uv in mesh.m_UV0:
            sb.append(f"vt {uv.x} {uv.y}")
        
        for sub in mesh.m_SubMeshes:
            indices = mesh.m_Indices[sub.firstByte // 2 : (sub.firstByte + sub.indexCount * 2) // 2]
            for i in range(0, len(indices), 3):
                v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                sb.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
        return "\n".join(sb)
    except: return ""

def dump_obj_to_dict(obj_data) -> dict:
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"): return obj_data.read_typetree()
    except: pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'm_Vertices', 'm_Normals', 'm_UV0', 'm_Indices']: continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)): out[attr] = val
        except: pass
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
            label = "Atlas Sheet" if safe_name.lower().endswith('.atlas') else "TextAsset"
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t

        elif t == "Mesh":
            obj_str = export_mesh_to_obj(data)
            if obj_str: return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "3D Mesh"

        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                name = list(samples.keys())[0]
                return name, samples[name], f"Audio/{name}", "Audio"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1: raw = raw_env_data[max(0, match-4):max(0, match-4)+12000000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video"

        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            if len(raw) > 10:
                ext = ".otf" if raw.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", "Font"

        tree_data = dump_obj_to_dict(data)
        if tree_data:
            return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"{t}/{safe_name}.json", t
    except: pass
    return None

def decode_astc_complex(rgb_data: bytes, sa_data: bytes = None) -> bytes:
    w = struct.unpack('<I', rgb_data[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_data[10:13] + b'\x00')[0]
    bw, bh = rgb_data[4], rgb_data[5]
    
    decoded_rgb = texture2ddecoder.decode_astc(rgb_data[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), decoded_rgb)
    
    if sa_data:
        decoded_alpha = texture2ddecoder.decode_astc(sa_data[16:], w, h, bw, bh)
        img_alpha = Image.frombytes("RGBA", (w, h), decoded_alpha)
        r, g, b, _ = img_rgb.split()
        a_chan, _, _, _ = img_alpha.split()
        img_rgb = Image.merge("RGBA", (r, g, b, a_chan))
    
    out = io.BytesIO()
    img_rgb.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'): return jsonify({"error": "No cache"}), 400
        indices = request.args.get('indices', '')
        idx_set = set(int(i) for i in indices.split(',') if i.strip()) if indices else None
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set is not None and i not in idx_set: continue
                zf.writestr(item['zip_path'], item['bytes'])
        
        zip_io.seek(0)
        orig = GLOBAL_CACHE_REGISTRY.get('original_name', 'assets')
        clean_name = re.split(r'[.\-]', orig)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=clean_name)

    if download_type == 'single':
        idx = int(request.args.get('file_index', -1))
        item = GLOBAL_CACHE_REGISTRY['extracted'][idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    files = request.files.getlist('asset_bundle')
    if not files: return jsonify({"error": "No files"}), 400

    rgb_file = next((f for f in files if '_rgb.astc' in f.filename.lower() or (f.filename.lower().endswith('.astc') and '_sa' not in f.filename.lower())), None)
    sa_file = next((f for f in files if '_sa.astc' in f.filename.lower()), None)

    extracted_list = []
    manifest = []
    
    try:
        if rgb_file and rgb_file.filename.lower().endswith('.astc'):
            rgb_data = rgb_file.read()
            sa_data = sa_file.read() if sa_file else None
            png = decode_astc_complex(rgb_data, sa_data)
            name = rgb_file.filename.replace('.astc', '.png')
            extracted_list.append({'name': name, 'zip_path': f"Textures/{name}", 'bytes': png})
            manifest.append({'index': 0, 'name': name, 'label': 'Texture2D (ASTC)'})
            GLOBAL_CACHE_REGISTRY['original_name'] = rgb_file.filename
        else:
            raw_bytes = files[0].read()
            decomp = decompress_stream(raw_bytes)
            if decomp.startswith(b'\xABKTX 11'):
                # KTX flipping logic
                w = struct.unpack('<I', decomp[36:40])[0]
                h = struct.unpack('<I', decomp[40:44])[0]
                kv = struct.unpack('<I', decomp[60:64])[0]
                dec = texture2ddecoder.decode_astc(decomp[64+kv+4:], w, h, 4, 4)
                img = Image.frombytes("RGBA", (w, h), dec)
                r,g,b,a = img.split()
                img = Image.merge("RGBA", (b,g,r,a)).transpose(Image.FLIP_TOP_BOTTOM)
                out = io.BytesIO(); img.save(out, format="PNG")
                name = files[0].filename.split('.')[0] + ".png"
                extracted_list.append({'name': name, 'zip_path': f"Textures/{name}", 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': name, 'label': 'Texture2D (KTX)'})
            else:
                env = UnityPy.load(decomp)
                for i, obj in enumerate(env.objects):
                    res = process_object_unrestricted(obj, decomp)
                    if res:
                        fname, fbytes, zpath, label = res
                        extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                        manifest.append({'index': len(manifest), 'name': fname, 'label': label})
            GLOBAL_CACHE_REGISTRY['original_name'] = files[0].filename

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": manifest})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)