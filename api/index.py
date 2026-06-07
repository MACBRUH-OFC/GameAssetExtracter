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
logging.basicConfig(level=logging.INFO)

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
    """Robust Unity Mesh to OBJ Exporter."""
    try:
        # Attempt to get data via UnityPy helper properties
        # In newer UnityPy versions, these properties handle the heavy lifting
        verts = getattr(mesh, "vertices", [])
        indices = getattr(mesh, "indices", [])
        normals = getattr(mesh, "normals", [])
        uvs = getattr(mesh, "uv", [])

        if not verts or len(verts) == 0:
            # Fallback for older Unity versions or stripped meshes
            return ""

        sb = []
        sb.append(f"# Exported by Assets Extractor")
        sb.append(f"o {mesh.name}")

        # Vertices: Unity is Left-Handed, OBJ is Right-Handed usually. 
        # Flip X to match most 3D software import expectations.
        for v in verts:
            sb.append(f"v {-v.x:.6f} {v.y:.6f} {v.z:.6f}")

        # UVs
        if uvs is not None and len(uvs) > 0:
            for uv in uvs:
                sb.append(f"vt {uv.x:.6f} {uv.y:.6f}")

        # Normals
        if normals is not None and len(normals) > 0:
            for n in normals:
                sb.append(f"vn {-n.x:.6f} {n.y:.6f} {n.z:.6f}")

        # Faces: Unity uses clockwise winding. OBJ uses counter-clockwise.
        # We reverse the order (v1, v3, v2) to fix orientation.
        for i in range(0, len(indices), 3):
            if i + 2 < len(indices):
                v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                if uvs is not None and len(uvs) > 0:
                    sb.append(f"f {v1}/{v1}/{v1} {v3}/{v3}/{v3} {v2}/{v2}/{v2}")
                else:
                    sb.append(f"f {v1} {v3} {v2}")
        
        return "\n".join(sb)
    except Exception as e:
        logging.error(f"Mesh export error: {e}")
        return ""

def dump_node_data(obj_data):
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    return {"m_Name": getattr(obj_data, "name", "Unnamed Object"), "type": str(type(obj_data))}

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        if obj.type in [ClassIDType.Transform, ClassIDType.RectTransform]:
            return None
            
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        # 1. Mesh Handling
        if t == "Mesh":
            obj_content = export_mesh_to_obj(data)
            if obj_content:
                return f"{safe_name}.obj", obj_content.encode('utf-8'), f"Meshes/{safe_name}.obj", "3D Mesh"
            return f"{safe_name}_mesh.json", json.dumps(dump_node_data(data)).encode(), f"Meshes/Metadata/{safe_name}.json", "Mesh Data"

        # 2. Texture & Sprites
        elif t in ["Texture2D", "Sprite"]:
            if hasattr(data, 'image'):
                img = data.image
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
            
        # 3. Audio
        elif t == "AudioClip":
            samples = getattr(data, "samples", {})
            if samples:
                name = list(samples.keys())[0]
                return name, samples[name], f"Audio/{name}", "Audio"
            raw_audio = obj.get_raw_data()
            ext = ".ogg" if raw_audio.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw_audio, f"Audio/{safe_name}{ext}", "Audio"

        # 4. Text & Configs
        elif t == "TextAsset":
            script = getattr(data, "m_Script", b"")
            if isinstance(script, str): script = script.encode('utf-8', errors='replace')
            ext = ".txt"
            if safe_name.endswith(".json") or script.startswith((b'{', b'[')): ext = ".json"
            elif ".atlas" in safe_name.lower(): ext = ".atlas"
            return f"{safe_name}{ext}", script, f"Config/{safe_name}{ext}", "TextAsset"

        # 5. Video
        elif t == "VideoClip":
            m_video = getattr(data, "m_VideoData", b"")
            if not m_video: m_video = obj.get_raw_data()
            return f"{safe_name}.mp4", m_video, f"Video/{safe_name}.mp4", "Video"

        # 6. Logic & Shaders
        elif t in ["MonoBehaviour", "GameObject", "Material", "Shader", "AnimationClip", "AnimatorController", "Animator"]:
            tree = dump_node_data(data)
            return f"{safe_name}.json", json.dumps(tree, indent=2).encode('utf-8'), f"Logic/{t}/{safe_name}.json", t

        # 7. Fonts
        elif t == "Font":
            f_data = getattr(data, "m_FontData", b"")
            if f_data:
                ext = ".otf" if f_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", f_data, f"Fonts/{safe_name}{ext}", "Font"

        # 8. Fallback
        raw_fallback = obj.get_raw_data()
        if raw_fallback and len(raw_fallback) > 0:
            return f"{safe_name}.dat", raw_fallback, f"Raw/{t}/{safe_name}.dat", t
            
    except Exception as e:
        logging.error(f"Process error on {obj.path_id}: {e}")
    return None

def decode_astc_to_png(rgb_bytes, sa_bytes=None):
    if not rgb_bytes.startswith(b'\x13\xab\xa1\x5c'): return None
    bw, bh = rgb_bytes[4], rgb_bytes[5]
    w = struct.unpack('<I', rgb_bytes[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_bytes[10:13] + b'\x00')[0]
    
    rgb_dec = texture2ddecoder.decode_astc(rgb_bytes[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), rgb_dec)
    
    if sa_bytes and sa_bytes.startswith(b'\x13\xab\xa1\x5c'):
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
    
    # Download Routine
    if dtype in ['zip', 'zip_filtered']:
        if 'extracted' not in GLOBAL_CACHE_REGISTRY: 
            return jsonify({"error": "Session expired or empty"}), 400
        indices = request.args.get('indices', '')
        idx_set = set(int(i) for i in indices.split(',') if i.strip()) if indices else None
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set is not None and idx not in idx_set: continue
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        
        orig = GLOBAL_CACHE_REGISTRY.get('original_name', 'Assets')
        clean_name = re.split(r'[.\-]', orig)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=clean_name)

    if 'asset_bundle' not in request.files: 
        return jsonify({"error": "No file uploaded"}), 400
    
    upload_files = request.files.getlist('asset_bundle')
    extracted_list = []
    manifest = []
    seen_md5 = set()
    
    # Check for ASTC pairing
    astc_files = [f for f in upload_files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        alpha = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        try:
            img = decode_astc_to_png(rgb.read(), alpha.read() if alpha else None)
            if img:
                out = io.BytesIO()
                img.save(out, format="PNG")
                extracted_list.append({'name': 'astc_decoded.png', 'zip_path': 'Textures/astc_decoded.png', 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': 'astc_decoded.png', 'label': 'Texture2D'})
        except Exception as e:
            return jsonify({"error": f"ASTC Pair Error: {str(e)}"}), 500
    else:
        # Standard Unity File
        u_file = upload_files[0]
        raw_data = u_file.read()
        decompressed = decompress_stream(raw_data)
        
        # Check if KTX
        if decompressed.startswith(b'\xABKTX 11'):
            try:
                w = struct.unpack('<I', decompressed[36:40])[0]
                h = struct.unpack('<I', decompressed[40:44])[0]
                kv_len = struct.unpack('<I', decompressed[60:64])[0]
                pixel_start = 64 + kv_len + 4
                pixel_data = decompressed[pixel_start:]
                dec = texture2ddecoder.decode_etc1(pixel_data, w, h)
                img = Image.frombytes("RGBA", (w, h), dec)
                # KTX is typically BGR + Flipped
                b, g, r, a = img.split()
                img = Image.merge("RGBA", (r, g, b, a)).transpose(Image.FLIP_TOP_BOTTOM)
                out = io.BytesIO()
                img.save(out, format="PNG")
                extracted_list.append({'name': 'ktx_decoded.png', 'zip_path': 'Textures/ktx_decoded.png', 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': 'ktx_decoded.png', 'label': 'Texture2D'})
            except: pass
        else:
            try:
                env = UnityPy.load(decompressed)
                idx_counter = 0
                for obj in env.objects:
                    res = process_object_unrestricted(obj, decompressed)
                    if res:
                        fname, fbytes, zpath, label = res
                        m5 = hashlib.md5(fbytes).hexdigest()
                        if m5 not in seen_md5:
                            seen_md5.add(m5)
                            extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                            manifest.append({'index': idx_counter, 'name': fname, 'label': label})
                            idx_counter += 1
                del env
                gc.collect()
            except Exception as e:
                return jsonify({"error": f"Unity Engine Error: {str(e)}"}), 500

    GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
    GLOBAL_CACHE_REGISTRY['original_name'] = upload_files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    try:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "Internal Error: index.html not found.", 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)