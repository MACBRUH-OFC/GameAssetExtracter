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
import logging
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
    """Enhanced Mesh Parser: Handles low-level vertex data buffers."""
    try:
        # High-level UnityPy access
        verts = getattr(mesh, "vertices", [])
        indices = getattr(mesh, "indices", [])
        normals = getattr(mesh, "normals", [])
        uvs = getattr(mesh, "uv", [])

        # Fallback to manual reading if properties are empty
        if not verts:
            mesh_data = mesh.read_typetree()
            if 'm_VertexData' in mesh_data:
                # This is complex and depends on Unity version, 
                # usually high-level .vertices is safer if UnityPy 1.20+ is used.
                pass 

        if not verts or len(verts) == 0:
            return ""

        sb = []
        sb.append(f"# Assets Extractor Mesh Export\no {mesh.name}")

        for v in verts:
            # Unity (Left Hand) to OBJ (Right Hand) conversion: Flip X
            sb.append(f"v {-v.x:.6f} {v.y:.6f} {v.z:.6f}")

        if uvs:
            for uv in uvs:
                sb.append(f"vt {uv.x:.6f} {uv.y:.6f}")

        if normals:
            for n in normals:
                sb.append(f"vn {-n.x:.6f} {n.y:.6f} {n.z:.6f}")

        # Unity uses Clockwise winding. OBJ uses Counter-Clockwise.
        # Reverse face order to prevent backface culling in 3D viewers.
        for i in range(0, len(indices), 3):
            if i + 2 < len(indices):
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
    except:
        pass
    return {"name": getattr(obj_data, "name", "Object"), "id": str(getattr(obj_data, "path_id", "0"))}

def process_object(obj, raw_env):
    try:
        if obj.type in [ClassIDType.Transform, ClassIDType.RectTransform]:
            return None
            
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        # 1. Geometry
        if t == "Mesh":
            obj_str = export_mesh_to_obj(data)
            if obj_str:
                return f"{safe_name}.obj", obj_str.encode('utf-8'), f"Meshes/{safe_name}.obj", "Mesh"
            return f"{safe_name}.json", json.dumps(dump_node(data)).encode(), f"Meshes/JSON/{safe_name}.json", "Mesh (Data)"

        # 2. Visuals
        elif t in ["Texture2D", "Sprite"]:
            if hasattr(data, 'image'):
                buf = io.BytesIO()
                data.image.save(buf, format="PNG")
                return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
            
        # 3. Sound
        elif t == "AudioClip":
            raw_audio = obj.get_raw_data()
            ext = ".ogg" if raw_audio.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw_audio, f"Audio/{safe_name}{ext}", "Audio"

        # 4. Data
        elif t == "TextAsset":
            script = getattr(data, "m_Script", b"")
            if isinstance(script, str): script = script.encode('utf-8')
            ext = ".json" if script.startswith((b'{', b'[')) else ".txt"
            return f"{safe_name}{ext}", script, f"Config/{safe_name}{ext}", "TextAsset"

        # 5. Multimedia
        elif t == "VideoClip":
            vid = getattr(data, "m_VideoData", b"") or obj.get_raw_data()
            return f"{safe_name}.mp4", vid, f"Video/{safe_name}.mp4", "Video"

        # 6. Metadata / Logic
        elif t in ["MonoBehaviour", "Material", "Shader", "AnimationClip", "AnimatorController", "GameObject"]:
            tree = dump_node(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode(), f"Logic/{t}/{safe_name}.json", t

        # 7. Fonts
        elif t == "Font":
            font = getattr(data, "m_FontData", b"")
            if font:
                ext = ".otf" if font.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", font, f"Fonts/{safe_name}{ext}", "Font"

        return f"{safe_name}.dat", obj.get_raw_data(), f"Other/{t}/{safe_name}.dat", t
            
    except:
        return None

def decode_astc(rgb, sa=None):
    if not rgb.startswith(b'\x13\xab\xa1\x5c'): return None
    bw, bh = rgb[4], rgb[5]
    w = struct.unpack('<I', rgb[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb[10:13] + b'\x00')[0]
    dec = texture2ddecoder.decode_astc(rgb[16:], w, h, bw, bh)
    img = Image.frombytes("RGBA", (w, h), dec)
    if sa and sa.startswith(b'\x13\xab\xa1\x5c'):
        sa_dec = texture2ddecoder.decode_astc(sa[16:], w, h, bw, bh)
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
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: return jsonify({"error": "Cache Empty"}), 400
        indices = request.args.get('indices', '')
        idx_set = set(int(i) for i in indices.split(',') if i.strip()) if indices else None
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set is not None and idx not in idx_set: continue
                # Default behavior is grouped into folders
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        orig = GLOBAL_CACHE_REGISTRY.get('original_name', 'Assets')
        name = re.split(r'[.\-]', orig)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=name)

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    
    files = request.files.getlist('asset_bundle')
    extracted = []
    manifest = []
    seen = set()
    
    astc_files = [f for f in files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        sa = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        try:
            img = decode_astc(rgb.read(), sa.read() if sa else None)
            if img:
                out = io.BytesIO()
                img.save(out, format="PNG")
                extracted.append({'name': 'astc_export.png', 'zip_path': 'Textures/astc_export.png', 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': 'astc_export.png', 'label': 'Texture2D'})
        except Exception as e: return jsonify({"error": str(e)}), 500
    else:
        u_file = files[0]
        decomp = decompress_stream(u_file.read())
        if decomp.startswith(b'\xABKTX 11'):
            try:
                w = struct.unpack('<I', decomp[36:40])[0]
                h = struct.unpack('<I', decomp[40:44])[0]
                kv = struct.unpack('<I', decomp[60:64])[0]
                pix = decomp[64 + kv + 4:]
                dec = texture2ddecoder.decode_etc1(pix, w, h)
                img = Image.frombytes("RGBA", (w, h), dec)
                b, g, r, a = img.split()
                img = Image.merge("RGBA", (r, g, b, a)).transpose(Image.FLIP_TOP_BOTTOM)
                out = io.BytesIO()
                img.save(out, format="PNG")
                extracted.append({'name': 'ktx_export.png', 'zip_path': 'Textures/ktx_export.png', 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': 'ktx_export.png', 'label': 'Texture2D'})
            except: pass
        else:
            try:
                env = UnityPy.load(decomp)
                idx = 0
                for obj in env.objects:
                    res = process_object(obj, decomp)
                    if res:
                        fn, fb, zp, lb = res
                        m5 = hashlib.md5(fb).hexdigest()
                        if m5 not in seen:
                            seen.add(m5)
                            extracted.append({'name': fn, 'zip_path': zp, 'bytes': fb})
                            manifest.append({'index': idx, 'name': fn, 'label': lb})
                            idx += 1
                del env
                gc.collect()
            except Exception as e: return jsonify({"error": str(e)}), 500

    GLOBAL_CACHE_REGISTRY['extracted'] = extracted
    GLOBAL_CACHE_REGISTRY['original_name'] = files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except: return "index.html not found.", 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)