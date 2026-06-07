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
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'm_Vertices', 'm_Indices']:
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

def export_mesh_to_obj(data) -> bytes:
    try:
        sb = []
        mesh_name = getattr(data, 'name', 'Mesh')
        sb.append(f"# Exported from Assets Extractor\ng {mesh_name}")
        
        # Vertices
        if hasattr(data, 'm_Vertices'):
            verts = data.m_Vertices
            for i in range(0, len(verts), 3):
                if i + 2 < len(verts):
                    sb.append(f"v {verts[i]} {verts[i+1]} {verts[i+2]}")
        
        # Indices (Faces)
        if hasattr(data, 'm_Indices'):
            inds = data.m_Indices
            for i in range(0, len(inds), 3):
                if i + 2 < len(inds):
                    # OBJ is 1-based indexing
                    sb.append(f"f {inds[i]+1} {inds[i+1]+1} {inds[i+2]+1}")
        
        return "\n".join(sb).encode('utf-8')
    except:
        return b""

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", pristine_name)
        
        # 1. Text Assets
        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".txt"
            label = "TextAsset"
            if safe_name.lower().endswith('.atlas') or raw.startswith(b"\n") or b"size:" in raw:
                ext = ".atlas"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
            return f"{safe_name}{ext}", raw, f"TextAssets/{safe_name}{ext}", label
            
        # 2. Textures / Sprites
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
            
        # 3. Audio
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "AudioClip"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "AudioClip"
            
        # 4. Video
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Videos/{safe_name}.mp4", "VideoClip"
            
        # 5. Meshes (Using working old logic)
        elif t == "Mesh":
            obj_data = export_mesh_to_obj(data)
            if obj_data:
                return f"{safe_name}.obj", obj_data, f"Models/{safe_name}.obj", "Mesh"
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_mesh.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Models/JSON/{safe_name}.json", "Mesh"
            
        # 6. Fonts
        elif t in ["Font", "TrueTypeFont"]:
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".otf" if raw_font_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font"
                
        # 7. Logic/Configs/Mono
        elif t in ["Shader", "Material", "MonoBehaviour", "AnimatorController", "AnimationClip", "AssetBundle", "SkinnedMeshRenderer", "MeshRenderer", "Animator", "GameObject"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}.json", js_bytes, f"{t}s/{safe_name}.json", t
            
        # 8. Catch-all
        else:
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", t
                
    except:
        pass
    return None

def decode_astc_dual_stream(rgb_bytes, alpha_bytes=None):
    # RGB
    w = struct.unpack('<I', rgb_bytes[7:10] + b'\x00')[0]
    h = struct.unpack('<I', rgb_bytes[10:13] + b'\x00')[0]
    bw, bh = rgb_bytes[4], rgb_bytes[5]
    
    rgb_dec = texture2ddecoder.decode_astc(rgb_bytes[16:], w, h, bw, bh)
    img_rgb = Image.frombytes("RGBA", (w, h), rgb_dec)
    
    if alpha_bytes:
        # Alpha/SA
        alpha_dec = texture2ddecoder.decode_astc(alpha_bytes[16:], w, h, bw, bh)
        img_alpha = Image.frombytes("RGBA", (w, h), alpha_dec)
        r, g, b, _ = img_rgb.split()
        a_chan, _, _, _ = img_alpha.split()
        final_img = Image.merge("RGBA", (r, g, b, a_chan))
    else:
        final_img = img_rgb
        
    out = io.BytesIO()
    final_img.save(out, format="PNG")
    return out.getvalue()

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n':
        raise Exception("Invalid KTX")
    gl_fmt = struct.unpack('<I', header[28:32])[0]
    w = struct.unpack('<I', header[36:40])[0]
    h = struct.unpack('<I', header[40:44])[0]
    kv_len = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + kv_len)
    img_size = struct.unpack('<I', f.read(4))[0]
    data = f.read(img_size)
    if gl_fmt == 0x8D64:
        decoded = texture2ddecoder.decode_etc1(data, w, h)
    elif 0x93B0 <= gl_fmt <= 0x93BD:
        astc_f = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = astc_f[gl_fmt]
        decoded = texture2ddecoder.decode_astc(data, w, h, bx, by)
    else:
        decoded = data[:w*h*4]
    img = Image.frombytes("RGBA", (w, h), decoded)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a)).transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    
    if download_type in ['zip', 'zip_filtered']:
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache is unpopulated."}), 400
        
        # Indices for filtered download
        indices_str = request.args.get('indices', '')
        idx_set = set(int(x) for x in indices_str.split(',') if x) if indices_str else None
        
        zip_io = io.BytesIO()
        # Note: All ZIP downloads from this updated tool are Grouped/Mapped by default
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if idx_set is not None and idx not in idx_set:
                    continue
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        
        orig_filename = GLOBAL_CACHE_REGISTRY.get('original_name', 'assets')
        clean_name = re.split(r'[.\-]', orig_filename)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=clean_name)
    
    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Index out of bounds."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    try:
        files = request.files.getlist('asset_bundle')
        extracted_list = []
        json_metadata_manifest = []
        seen_md5 = set()
        
        # ASTC Pairing Logic
        astc_files = [f for f in files if f.filename.lower().endswith('.astc')]
        if astc_files:
            rgb_file = next((f for f in astc_files if 'rgb' in f.filename.lower()), astc_files[0])
            sa_file = next((f for f in astc_files if 'sa' in f.filename.lower() or 'alpha' in f.filename.lower()), None)
            
            png = decode_astc_dual_stream(rgb_file.read(), sa_file.read() if sa_file else None)
            clean_base = os.path.splitext(rgb_file.filename)[0]
            extracted_list.append({'name': f"{clean_base}.png", 'zip_path': f"Textures/{clean_base}.png", 'bytes': png, 'label': 'Texture2D'})
            json_metadata_manifest.append({'index': 0, 'name': f"{clean_base}.png", 'label': 'Texture2D'})
            GLOBAL_CACHE_REGISTRY['original_name'] = rgb_file.filename
        else:
            # Unity/KTX Logic
            uploaded_file = files[0]
            raw_bytes = uploaded_file.read()
            decompressed_data = decompress_stream(raw_bytes)
            
            if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
                png = convert_ktx_to_png_fallback(decompressed_data)
                clean_name = os.path.splitext(uploaded_file.filename)[0] + ".png"
                extracted_list.append({'name': clean_name, 'zip_path': f"Textures/{clean_name}", 'bytes': png, 'label': 'Texture2D'})
                json_metadata_manifest.append({'index': 0, 'name': clean_name, 'label': 'Texture2D'})
            else:
                env = UnityPy.load(decompressed_data)
                counter = 0
                for obj in env.objects:
                    res = process_object_unrestricted(obj, decompressed_data)
                    if res:
                        fname, fbytes, zpath, tlabel = res
                        h = hashlib.md5(fbytes).hexdigest()
                        if h not in seen_md5:
                            seen_md5.add(h)
                            extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes, 'label': tlabel})
                            json_metadata_manifest.append({'index': counter, 'name': fname, 'label': tlabel})
                            counter += 1
                del env
                gc.collect()
            GLOBAL_CACHE_REGISTRY['original_name'] = uploaded_file.filename

        if not extracted_list:
            return jsonify({"error": "No valid assets found."}), 400
            
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Mapping Missing: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)