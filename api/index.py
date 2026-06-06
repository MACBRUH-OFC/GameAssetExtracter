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

# Force No GUI for server environments
os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)

# Base directory for template pathing
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    """Recursively decompress Gzip and Zlib streams."""
    try:
        if data.startswith(b'\x1f\x8b'):
            return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
            return decompress_stream(zlib.decompress(data))
    except Exception:
        pass
    return data

def extract_clean_name(obj, data, default_type: str) -> str:
    """Extract the most accurate name from Unity Objects."""
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"{default_type}_{obj.path_id}"

def export_mesh_to_obj(mesh) -> str:
    """Robust conversion of Unity Mesh objects to standard Wavefront OBJ format."""
    try:
        # Extract geometry using UnityPy helper properties
        verts = mesh.vertices
        indices = mesh.indices
        normals = mesh.normals
        uvs = mesh.uv
        
        if not verts:
            return ""
            
        sb = []
        sb.append(f"# Exported via MACBRUH_FF Assets Extractor")
        sb.append(f"o {mesh.name}")
        
        # Write Vertices (Unity uses a Left-Handed system, OBJ is Right-Handed)
        for v in verts:
            sb.append(f"v {-v.x} {v.y} {v.z}")
            
        # Write UVs
        if uvs:
            for uv in uvs:
                sb.append(f"vt {uv.x} {uv.y}")
        else:
            # Fallback UV to prevent index errors
            sb.append("vt 0.0 0.0")
            
        # Write Normals
        if normals:
            for n in normals:
                sb.append(f"vn {-n.x} {n.y} {n.z}")
        else:
            sb.append("vn 0.0 1.0 0.0")

        # Write Faces (Unity index is 0-based, OBJ is 1-based)
        # Unity faces are typically clockwise, so we flip v2 and v3 for OBJ
        for i in range(0, len(indices), 3):
            v1, v2, v3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
            if uvs and normals:
                sb.append(f"f {v1}/{v1}/{v1} {v2}/{v2}/{v2} {v3}/{v3}/{v3}")
            elif uvs:
                sb.append(f"f {v1}/{v1} {v2}/{v2} {v3}/{v3}")
            else:
                sb.append(f"f {v1} {v2} {v3}")
                
        return "\n".join(sb)
    except Exception as e:
        print(f"Mesh export error: {e}")
        return ""

def dump_typetree(obj_data) -> dict:
    """Recursively dump Unity Typetree data into a dictionary."""
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except Exception:
        pass
    
    # Fallback to manual attribute dumping
    out = {}
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']:
            continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)):
                out[attr] = val
        except:
            pass
    return out

def process_object_unrestricted(obj, raw_env_data: bytes):
    """Router to handle and convert all supported Unity asset types."""
    try:
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        # 1. Textures & Sprites
        if t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", t

        # 2. 3D Geometry
        elif t == "Mesh":
            obj_content = export_mesh_to_obj(data)
            if obj_content:
                return f"{safe_name}.obj", obj_content.encode('utf-8'), f"Meshes/{safe_name}.obj", "Mesh (OBJ)"
            return f"{safe_name}.json", json.dumps(dump_typetree(data)).encode(), f"Meshes/{safe_name}.json", "Mesh (Data)"

        # 3. Audio Tracks
        elif t == "AudioClip":
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "AudioClip"

        # 4. Text & Configs
        elif t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            label = "TextAsset"
            if safe_name.lower().endswith('.atlas'): label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")): label = "JSON Config"
            return f"{safe_name}.txt", raw, f"Text/{safe_name}.txt", label

        # 5. Shaders & Visuals
        elif t in ["Shader", "Material"]:
            js = json.dumps(dump_typetree(data), indent=2).encode('utf-8')
            return f"{safe_name}.json", js, f"Shaders_Materials/{t}/{safe_name}.json", t

        # 6. Animation Systems
        elif t in ["AnimationClip", "AnimatorController", "Animator", "Avatar"]:
            js = json.dumps(dump_typetree(data), indent=2).encode('utf-8')
            return f"{safe_name}.json", js, f"Animations/{t}/{safe_name}.json", t

        # 7. Scene Hierarchy
        elif t in ["GameObject", "MonoBehaviour", "Transform"]:
            js = json.dumps(dump_typetree(data), indent=2).encode('utf-8')
            return f"{safe_name}.json", js, f"Hierarchy/{t}/{safe_name}.json", t

        # 8. Video Media
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    raw = raw_env_data[max(0, match-4):max(0, match-4) + 15_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "VideoClip"

        # 9. Typography
        elif t == "Font":
            raw = getattr(data, "m_FontData", b"")
            if raw:
                ext = ".otf" if raw.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw, f"Fonts/{safe_name}{ext}", "Font"

        # 10. Fallback for generic binary blocks
        raw_bin = obj.get_raw_data()
        if raw_bin:
            return f"{safe_name}.dat", raw_bin, f"Other/{t}/{safe_name}.dat", t
            
    except Exception:
        pass
    return None

def decode_astc_dual_stream(rgb_bytes, sa_bytes=None):
    """Pairs RGB and Alpha ASTC streams into a single PNG image."""
    if not rgb_bytes.startswith(b'\x13\xab\xa1\x5c'):
        return None
    
    bw = rgb_bytes[4]
    bh = rgb_bytes[5]
    w = struct.unpack('<I', rgb_bytes[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_bytes[10:13] + b'\x00')[0]
    
    # Decode RGB
    rgb_dec = texture2ddecoder.decode_astc(rgb_bytes[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), rgb_dec)
    
    if sa_bytes and sa_bytes.startswith(b'\x13\xab\xa1\x5c'):
        # Decode Alpha
        sa_dec = texture2ddecoder.decode_astc(sa_bytes[16:], w, h, bw, bh)
        img_sa = Image.frombytes("RGBA", (w, h), sa_dec)
        
        # Merge Alpha into RGB
        r, g, b, _ = img_rgb.split()
        a_chan, _, _, _ = img_sa.split()
        final_img = Image.merge("RGBA", (r, g, b, a_chan))
        out = io.BytesIO()
        final_img.save(out, format="PNG")
        return out.getvalue()
        
    out = io.BytesIO()
    img_rgb.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    dtype = request.args.get('download_type', '')
    
    # --- Zip Download Routines ---
    if dtype in ['zip', 'zip_filtered']:
        if 'extracted' not in GLOBAL_CACHE_REGISTRY:
            return jsonify({"error": "Session cache expired"}), 400
        
        indices = request.args.get('indices', '')
        idx_set = [int(i) for i in indices.split(',') if i.strip()] if indices else []
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set and i not in idx_set:
                    continue
                # Default is always Grouped/Mapped for ZIP
                zf.writestr(item['zip_path'], item['bytes'])
        
        zip_io.seek(0)
        orig_name = GLOBAL_CACHE_REGISTRY.get('original_name', 'Assets')
        clean_name = re.split(r'[.\-]', orig_name)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=clean_name)

    # --- Single File Download/Preview ---
    if dtype == 'single':
        f_idx = int(request.args.get('file_index', -1))
        if 'extracted' not in GLOBAL_CACHE_REGISTRY or f_idx < 0 or f_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Index out of bounds"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][f_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    # --- Upload Processing ---
    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No source file found"}), 400
    
    files = request.files.getlist('asset_bundle')
    extracted_list = []
    manifest = []
    seen_hashes = set()
    
    # Detect ASTC pairs
    astc_files = [f for f in files if f.filename.lower().endswith('.astc')]
    if astc_files:
        rgb = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
        alpha = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
        try:
            png = decode_astc_dual_stream(rgb.read(), alpha.read() if alpha else None)
            if png:
                extracted_list.append({'name': 'astc_merge.png', 'zip_path': 'Textures/astc_merge.png', 'bytes': png})
                manifest.append({'index': 0, 'name': 'astc_merge.png', 'label': 'Texture2D'})
        except Exception as e:
            return jsonify({"error": f"ASTC Decoding error: {e}"}), 500
    else:
        # Standard Unity Bundle Routine
        try:
            u_file = files[0]
            raw = u_file.read()
            decomp = decompress_stream(raw)
            
            # KTX Check
            if decomp.startswith(b'\xABKTX 11'):
                # Extract simple KTX to PNG
                w = struct.unpack('<I', decomp[36:40])[0]
                h = struct.unpack('<I', decomp[40:44])[0]
                kv = struct.unpack('<I', decomp[60:64])[0]
                pix = decomp[64 + kv + 4:]
                dec = texture2ddecoder.decode_etc1(pix, w, h)
                img = Image.frombytes("RGBA", (w, h), dec)
                # Swap BGR and Flip
                b, g, r, a = img.split()
                img = Image.merge("RGBA", (r, g, b, a)).transpose(Image.FLIP_TOP_BOTTOM)
                out = io.BytesIO()
                img.save(out, format="PNG")
                extracted_list.append({'name': 'ktx_export.png', 'zip_path': 'Textures/ktx_export.png', 'bytes': out.getvalue()})
                manifest.append({'index': 0, 'name': 'ktx_export.png', 'label': 'Texture2D'})
            else:
                env = UnityPy.load(decomp)
                counter = 0
                for obj in env.objects:
                    res = process_object_unrestricted(obj, decomp)
                    if res:
                        fname, fbytes, zpath, label = res
                        h = hashlib.md5(fbytes).hexdigest()
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                            manifest.append({'index': counter, 'name': fname, 'label': label})
                            counter += 1
                del env
                gc.collect()
        except Exception as e:
            return jsonify({"error": f"Engine initialization failed: {e}"}), 500
            
    GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
    GLOBAL_CACHE_REGISTRY['original_name'] = files[0].filename
    return jsonify({"files": manifest})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return "Critical UI asset (index.html) is missing from the server root.", 404

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=False)