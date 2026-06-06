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

def export_mesh_to_obj(data) -> str:
    try:
        vertices = data.m_Vertices
        indices = data.m_Indices if hasattr(data, 'm_Indices') else []
        if not indices and hasattr(data, 'm_SubMeshes'):
            indices = []
            for sub in data.m_SubMeshes:
                indices.extend(sub.indexArray)
        
        if not vertices: return ""
        
        sb = [f"g {data.name}"]
        for v in vertices:
            sb.append(f"v {v.x} {v.y} {v.z}")
        
        # Normals
        if hasattr(data, 'm_Normals') and data.m_Normals:
            for n in data.m_Normals:
                sb.append(f"vn {n.x} {n.y} {n.z}")
        
        # UVs
        if hasattr(data, 'm_UV0') and data.m_UV0:
            for uv in data.m_UV0:
                sb.append(f"vt {uv.x} {uv.y}")

        # Faces (Unity uses Clockwise, OBJ uses Counter-Clockwise usually, but simple f is enough)
        for i in range(0, len(indices), 3):
            if i + 2 < len(indices):
                v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                sb.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
        
        return "\n".join(sb)
    except: return ""

def dump_tree(obj_data):
    try:
        if hasattr(obj_data, "read_typetree"): return obj_data.read_typetree()
    except: pass
    return {"info": "Serialized data only"}

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            return f"{safe_name}.txt", raw, f"Text/{safe_name}.txt", "TextAsset"

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t

        elif t == "Mesh":
            obj_str = export_mesh_to_obj(data)
            if obj_str:
                return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "Mesh"
            return f"{safe_name}.json", json.dumps(dump_tree(data)).encode(), f"Meshes/{safe_name}.json", "Mesh (JSON)"

        elif t == "AudioClip":
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio"

        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            if raw:
                ext = ".otf" if raw.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", "Font"

        elif t in ["MonoBehaviour", "GameObject", "Material", "Shader", "AnimationClip"]:
            return f"{safe_name}.json", json.dumps(dump_tree(data)).encode(), f"{t}/{safe_name}.json", t

        return f"{safe_name}.dat", obj.get_raw_data(), f"Other/{t}/{safe_name}.dat", t
    except: return None

def decode_astc_complex(rgb_data, alpha_data=None):
    # Header: 13 ab a1 5c
    bw, bh = rgb_data[4], rgb_data[5]
    w = struct.unpack('<I', rgb_data[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_data[10:13] + b'\x00')[0]
    
    rgb_dec = texture2ddecoder.decode_astc(rgb_data[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), rgb_dec)
    
    if alpha_data:
        alpha_dec = texture2ddecoder.decode_astc(alpha_data[16:], w, h, bw, bh)
        img_alpha = Image.frombytes("RGBA", (w, h), alpha_dec)
        # Use Red channel of alpha texture as the Alpha channel for RGB
        r, g, b, _ = img_rgb.split()
        a, _, _, _ = img_alpha.split()
        final_img = Image.merge("RGBA", (r, g, b, a))
    else:
        final_img = img_rgb
        
    out = io.BytesIO()
    final_img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_extraction():
    global GLOBAL_CACHE_REGISTRY
    dtype = request.args.get('download_type', '')
    
    if dtype in ['zip', 'zip_filtered']:
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: return jsonify({"error": "No cache"}), 400
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

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    
    files = request.files.getlist('asset_bundle')
    extracted_list = []
    manifest = []
    seen_md5 = set()
    
    # Handle ASTC Pairing
    astc_files = [f for f in files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        alpha = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        try:
            png_bytes = decode_astc_complex(rgb.read(), alpha.read() if alpha else None)
            extracted_list.append({'name': 'astc_export.png', 'zip_path': 'Textures/astc_export.png', 'bytes': png_bytes})
            manifest.append({'index': 0, 'name': 'astc_export.png', 'label': 'Texture2D'})
        except Exception as e: return jsonify({"error": f"ASTC Fail: {str(e)}"}), 500
    else:
        # Standard AssetBundle logic
        uploaded_file = files[0]
        raw_data = uploaded_file.read()
        decomp = decompress_stream(raw_data)
        
        if decomp.startswith(b'\xABKTX 11'):
            # Simple KTX
            w = struct.unpack('<I', decomp[36:40])[0]
            h = struct.unpack('<I', decomp[40:44])[0]
            kv = struct.unpack('<I', decomp[60:64])[0]
            pix = decomp[64 + kv + 4:]
            dec = texture2ddecoder.decode_etc1(pix, w, h)
            img = Image.frombytes("RGBA", (w, h), dec)
            r,g,b,a = img.split()
            img = Image.merge("RGBA", (b,g,r,a)).transpose(Image.FLIP_TOP_BOTTOM)
            out = io.BytesIO()
            img.save(out, format="PNG")
            extracted_list.append({'name': 'ktx_export.png', 'zip_path': 'Textures/ktx_export.png', 'bytes': out.getvalue()})
            manifest.append({'index': 0, 'name': 'ktx_export.png', 'label': 'Texture2D'})
        else:
            env = UnityPy.load(decomp)
            count = 0
            for obj in env.objects:
                res = process_object_unrestricted(obj, decomp)
                if res:
                    fname, fbytes, zpath, label = res
                    h_val = hashlib.md5(fbytes).hexdigest()
                    if h_val not in seen_md5:
                        seen_md5.add(h_val)
                        extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                        manifest.append({'index': count, 'name': fname, 'label': label})
                        count += 1
    
    GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
    GLOBAL_CACHE_REGISTRY['original_name'] = files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    with open('index.html', 'r', encoding='utf-8') as f: return f.read()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)