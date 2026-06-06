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
GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
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
        # Robust vertex extraction
        verts = mesh.m_Vertices
        if not verts: return ""
        
        sb = [f"g {mesh.m_Name}"]
        for v in verts:
            sb.append(f"v {-v.x:.6f} {v.y:.6f} {v.z:.6f}")
        
        # Normals and UVs
        if hasattr(mesh, 'm_Normals') and mesh.m_Normals:
            for n in mesh.m_Normals:
                sb.append(f"vn {-n.x:.4f} {n.y:.4f} {n.z:.4f}")
        if hasattr(mesh, 'm_UV0') and mesh.m_UV0:
            for uv in mesh.m_UV0:
                sb.append(f"vt {uv.x:.4f} {uv.y:.4f}")

        # Submesh Index Processing
        for sub in mesh.m_SubMeshes:
            indices = sub.indexArray
            for i in range(0, len(indices), 3):
                v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                sb.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
        
        return "\n".join(sb)
    except: return ""

def dump_obj(data):
    try:
        if hasattr(data, "read_typetree"): return data.read_typetree()
    except: pass
    return {"m_Name": getattr(data, "name", "Unknown")}

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8')
            return f"{safe_name}.txt", raw, f"Text/{safe_name}.txt", "TextAsset"

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t

        elif t == "Mesh":
            obj_str = export_mesh_to_obj(data)
            if obj_str:
                return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "Mesh"
            return f"{safe_name}.json", json.dumps(dump_obj(data)).encode(), f"Meshes/{safe_name}.json", "Mesh Data"

        elif t == "AudioClip":
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "AudioClip"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "VideoClip"

        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            ext = ".otf" if raw.startswith(b'OTTO') else ".ttf"
            return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", "Font"

        elif t in ["MonoBehaviour", "Material", "Shader", "AnimationClip", "AnimatorController", "GameObject"]:
            return f"{safe_name}.json", json.dumps(dump_obj(data)).encode(), f"{t}/{safe_name}.json", t

        return f"{safe_name}.dat", obj.get_raw_data(), f"Other/{t}/{safe_name}.dat", t
    except: return None

def decode_astc_pair(rgb_bytes, sa_bytes=None):
    bw, bh = rgb_bytes[4], rgb_bytes[5]
    w = struct.unpack('<I', rgb_bytes[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_bytes[10:13] + b'\x00')[0]
    
    rgb_dec = texture2ddecoder.decode_astc(rgb_bytes[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), rgb_dec)
    
    if sa_bytes:
        sa_dec = texture2ddecoder.decode_astc(sa_bytes[16:], w, h, bw, bh)
        img_sa = Image.frombytes("RGBA", (w, h), sa_dec)
        r, g, b, _ = img_rgb.split()
        a, _, _, _ = img_sa.split()
        return Image.merge("RGBA", (r, g, b, a))
    return img_rgb

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_extraction():
    global GLOBAL_CACHE_REGISTRY
    dtype = request.args.get('download_type', '')
    
    if dtype in ['zip', 'zip_filtered']:
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: return jsonify({"error": "Cache empty"}), 400
        indices = request.args.get('indices', '')
        idx_list = [int(i) for i in indices.split(',') if i.strip()] if indices else []
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_list and idx not in idx_list: continue
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        
        orig = GLOBAL_CACHE_REGISTRY.get('original_name', 'Assets')
        zip_name = re.split(r'[.\-]', orig)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=zip_name)

    if dtype == 'single':
        f_idx = int(request.args.get('file_index', -1))
        item = GLOBAL_CACHE_REGISTRY['extracted'][f_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    
    upload_files = request.files.getlist('asset_bundle')
    extracted_list = []
    manifest = []
    seen_md5 = set()
    
    astc_files = [f for f in upload_files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        sa = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        img = decode_astc_pair(rgb.read(), sa.read() if sa else None)
        out = io.BytesIO()
        img.save(out, format="PNG")
        extracted_list.append({'name': 'astc_merge.png', 'zip_path': 'Textures/astc_merge.png', 'bytes': out.getvalue()})
        manifest.append({'index': 0, 'name': 'astc_merge.png', 'label': 'Texture2D'})
    else:
        u_file = upload_files[0]
        raw = u_file.read()
        decomp = decompress_stream(raw)
        env = UnityPy.load(decomp)
        count = 0
        for obj in env.objects:
            res = process_object_unrestricted(obj, decomp)
            if res:
                fname, fbytes, zpath, label = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                    manifest.append({'index': count, 'name': fname, 'label': label})
                    count += 1
    
    GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
    GLOBAL_CACHE_REGISTRY['original_name'] = upload_files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except: return "index.html missing", 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)