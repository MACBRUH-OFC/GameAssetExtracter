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
import numpy as np

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy
from UnityPy.enums import ClassIDType

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
        base = os.path.basename(obj.container)
        if base: return os.path.splitext(base)[0]
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"{default_type}_{obj.path_id}"

def export_mesh_to_obj(mesh) -> str:
    try:
        verts = getattr(mesh, "vertices", [])
        indices = getattr(mesh, "indices", [])
        normals = getattr(mesh, "normals", [])
        uvs = getattr(mesh, "uv", [])

        if not verts or len(verts) == 0:
            return ""

        sb = [f"# Assets Extractor OBJ Export\no {mesh.name}"]
        for v in verts:
            sb.append(f"v {-v.x:.6f} {v.y:.6f} {v.z:.6f}")
        for uv in uvs:
            sb.append(f"vt {uv.x:.6f} {uv.y:.6f}")
        for n in normals:
            sb.append(f"vn {-n.x:.6f} {n.y:.6f} {n.z:.6f}")

        # Winding order correction for Unity to OBJ
        for i in range(0, len(indices), 3):
            v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
            if uvs:
                sb.append(f"f {v1}/{v1}/{v1} {v3}/{v3}/{v3} {v2}/{v2}/{v2}")
            else:
                sb.append(f"f {v1} {v3} {v2}")
        return "\n".join(sb)
    except:
        return ""

def dump_node(obj_data):
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except: pass
    return {"name": getattr(obj_data, "name", "Object")}

def process_object(obj, raw_env):
    try:
        if obj.type in [ClassIDType.Transform, ClassIDType.RectTransform]:
            return None
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        if t == "Mesh":
            obj_str = export_mesh_to_obj(data)
            if obj_str:
                return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "Mesh"
        elif t in ["Texture2D", "Sprite"]:
            if hasattr(data, 'image'):
                buf = io.BytesIO()
                data.image.save(buf, format="PNG")
                return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
        elif t == "AudioClip":
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio"
        elif t == "TextAsset":
            script = getattr(data, "m_Script", b"")
            if isinstance(script, str): script = script.encode('utf-8')
            ext = ".json" if script.startswith((b'{', b'[')) else ".txt"
            return f"{safe_name}{ext}", script, f"Config/{safe_name}{ext}", "TextAsset"
        elif t == "Font":
            font = getattr(data, "m_FontData", b"")
            if font:
                ext = ".otf" if font.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", font, f"Fonts/{safe_name}{ext}", "Font"
        elif t in ["MonoBehaviour", "Material", "Shader", "AnimationClip", "AnimatorController", "GameObject"]:
            tree = dump_node(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode(), f"{t}/{safe_name}.json", t
        
        return f"{safe_name}.dat", obj.get_raw_data(), f"Raw/{t}/{safe_name}.dat", t
    except: return None

def decode_astc_dual(rgb_data, sa_data=None):
    if not rgb_data.startswith(b'\x13\xab\xa1\x5c'): return None
    bw, bh = rgb_data[4], rgb_data[5]
    w = struct.unpack('<I', rgb_data[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_data[10:13] + b'\x00')[0]
    dec = texture2ddecoder.decode_astc(rgb_data[16:], w, h, bw, bh)
    img = Image.frombytes("RGBA", (w, h), dec)
    if sa_data and sa_data.startswith(b'\x13\xab\xa1\x5c'):
        sa_dec = texture2ddecoder.decode_astc(sa_data[16:], w, h, bw, bh)
        img_sa = Image.frombytes("RGBA", (w, h), sa_dec)
        r, g, b, _ = img.split()
        a, _, _, _ = img_sa.split()
        return Image.merge("RGBA", (r, g, b, a))
    return img

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_api():
    global GLOBAL_CACHE_REGISTRY
    dtype = request.args.get('download_type', '')
    if dtype in ['zip', 'zip_filtered']:
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: return jsonify({"error": "No cache"}), 400
        indices = request.args.get('indices', '')
        idx_set = set(int(i) for i in indices.split(',') if i.strip()) if indices else None
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set is not None and idx not in idx_set: continue
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        name = re.split(r'[.\-]', GLOBAL_CACHE_REGISTRY.get('original_name', 'Assets'))[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=name)

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    upload_files = request.files.getlist('asset_bundle')
    extracted = []
    manifest = []
    seen = set()
    
    astc_files = [f for f in upload_files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        sa = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        img = decode_astc_dual(rgb.read(), sa.read() if sa else None)
        if img:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            extracted.append({'name': 'astc_decoded.png', 'zip_path': 'Textures/astc_decoded.png', 'bytes': buf.getvalue()})
            manifest.append({'index': 0, 'name': 'astc_decoded.png', 'label': 'Texture2D'})
    else:
        u_file = upload_files[0]
        raw_bytes = u_file.read()
        decomp = decompress_stream(raw_bytes)
        if decomp.startswith(b'\xABKTX 11'):
            w = struct.unpack('<I', decomp[36:40])[0]
            h = struct.unpack('<I', decomp[40:44])[0]
            kv = struct.unpack('<I', decomp[60:64])[0]
            dec = texture2ddecoder.decode_etc1(decomp[64+kv+4:], w, h)
            img = Image.frombytes("RGBA", (w, h), dec)
            b,g,r,a = img.split()
            img = Image.merge("RGBA", (r,g,b,a)).transpose(Image.FLIP_TOP_BOTTOM)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            extracted.append({'name': 'ktx_decoded.png', 'zip_path': 'Textures/ktx_decoded.png', 'bytes': buf.getvalue()})
            manifest.append({'index': 0, 'name': 'ktx_decoded.png', 'label': 'Texture2D'})
        else:
            env = UnityPy.load(decomp)
            count = 0
            for obj in env.objects:
                res = process_object(obj, decomp)
                if res:
                    fn, fb, zp, lb = res
                    m5 = hashlib.md5(fb).hexdigest()
                    if m5 not in seen:
                        seen.add(m5)
                        extracted.append({'name': fn, 'zip_path': zp, 'bytes': fb})
                        manifest.append({'index': count, 'name': fn, 'label': lb})
                        count += 1
            del env
            gc.collect()

    GLOBAL_CACHE_REGISTRY['extracted'] = extracted
    GLOBAL_CACHE_REGISTRY['original_name'] = upload_files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f: return f.read()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)