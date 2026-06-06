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

def export_mesh_to_obj(mesh) -> str:
    try:
        sb = []
        sb.append(f"g {mesh.name}")
        for v in mesh.m_Vertices:
            sb.append(f"v {v.x} {v.y} {v.z}")
        for n in mesh.m_Normals:
            sb.append(f"vn {n.x} {n.y} {n.z}")
        for uv in mesh.m_UV0:
            sb.append(f"vt {uv.x} {uv.y}")
        for sub in mesh.m_SubMeshes:
            for i in range(0, len(sub.indexArray), 3):
                idx = sub.indexArray
                sb.append(f"f {idx[i]+1}/{idx[i]+1}/{idx[i]+1} {idx[i+1]+1}/{idx[i+1]+1}/{idx[i+1]+1} {idx[i+2]+1}/{idx[i+2]+1}/{idx[i+2]+1}")
        return "\n".join(sb)
    except:
        return ""

def dump_obj_to_dict(obj_data) -> dict:
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples', 'm_Vertices', 'm_Normals', 'm_UV0', 'm_Indices']:
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
            if safe_name.lower().endswith('.atlas'):
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            buf.close()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png", t

        elif t == "Mesh":
            obj_content = export_mesh_to_obj(data)
            if obj_content:
                return f"{safe_name}.obj", obj_content.encode('utf-8'), f"Meshes/{safe_name}.obj", "3D Mesh"
            else:
                tree_data = dump_obj_to_dict(data)
                return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Meshes/{safe_name}.json", "Mesh Data"

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
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video"

        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if len(raw_font_data) > 10:
                ext = ".otf" if raw_font_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font"
            
        elif t in ["MonoBehaviour", "GameObject", "Material", "Shader", "AssetBundle", "Animator", "AnimationClip"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}.json", js_bytes, f"{t}/{safe_name}.json", t

        else:
            try:
                tree_data = dump_obj_to_dict(data)
                if tree_data:
                    return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Other/{t}/{safe_name}.json", t
            except: pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", t
    except: pass
    return None

def decode_astc_to_png(data: bytes) -> bytes:
    if not data.startswith(b'\x13\xab\xa1\x5c'):
        raise Exception("Invalid ASTC header")
    block_width = data[4]
    block_height = data[5]
    width = struct.unpack('<I', data[7:10] + b'\x00')[0]
    height = struct.unpack('<I', data[10:13] + b'\x00')[0]
    actual_data = data[16:]
    decoded = texture2ddecoder.decode_astc(actual_data, width, height, block_width, block_height)
    img = Image.frombytes("RGBA", (width, height), decoded)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

def decode_ktx_to_png(data: bytes) -> bytes:
    header = data[:64]
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    kv_len = struct.unpack('<I', header[60:64])[0]
    pixel_data = data[64 + kv_len + 4:]
    if gl_internal_format == 0x93B0:
        decoded = texture2ddecoder.decode_astc(pixel_data, width, height, 4, 4)
    elif gl_internal_format == 0x8D64:
        decoded = texture2ddecoder.decode_etc1(pixel_data, width, height)
    else:
        decoded = pixel_data
    img = Image.frombytes("RGBA", (width, height), decoded)
    r,g,b,a = img.split()
    img = Image.merge("RGBA", (b,g,r,a)).transpose(Image.FLIP_TOP_BOTTOM)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    
    if download_type in ['zip', 'zip_grouped']:
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache empty"}), 400
        
        filtered_indices = request.args.get('indices', '')
        indices_list = [int(i) for i in filtered_indices.split(',') if i.strip()] if filtered_indices else []
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if indices_list and idx not in indices_list:
                    continue
                path = item['zip_path'] if download_type == 'zip_grouped' else item['name']
                zf.writestr(path, item['bytes'])
        
        zip_io.seek(0)
        base_name = GLOBAL_CACHE_REGISTRY.get('original_name', 'assets')
        clean_zip_name = re.split(r'[.\-]', base_name)[0] + "[Extracted].zip"
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=clean_zip_name)

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Invalid index"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        ext = item['name'].split('.')[-1].lower()
        mimetype = 'image/png' if ext == 'png' else 'application/octet-stream'
        return send_file(io.BytesIO(item['bytes']), mimetype=mimetype, as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file"}), 400

    try:
        uploaded_file = request.files['asset_bundle']
        orig_name = uploaded_file.filename
        raw_bytes = uploaded_file.read()
        decomp_data = decompress_stream(raw_bytes)
        
        extracted_list = []
        manifest = []
        seen_md5 = set()
        
        if decomp_data.startswith(b'\x13\xab\xa1\x5c'):
            png = decode_astc_to_png(decomp_data)
            extracted_list.append({'name': 'decoded_astc.png', 'zip_path': 'Textures/decoded_astc.png', 'bytes': png})
            manifest.append({'index': 0, 'name': 'decoded_astc.png', 'label': 'Texture2D'})
        elif decomp_data.startswith(b'\xABKTX 11'):
            png = decode_ktx_to_png(decomp_data)
            extracted_list.append({'name': 'decoded_ktx.png', 'zip_path': 'Textures/decoded_ktx.png', 'bytes': png})
            manifest.append({'index': 0, 'name': 'decoded_ktx.png', 'label': 'Texture2D'})
        else:
            env = UnityPy.load(decomp_data)
            counter = 0
            for obj in env.objects:
                res = process_object_unrestricted(obj, decomp_data)
                if res:
                    fname, fbytes, zpath, label = res
                    h = hashlib.md5(fbytes).hexdigest()
                    if h not in seen_md5:
                        seen_md5.add(h)
                        extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                        manifest.append({'index': counter, 'name': fname, 'label': label})
                        counter += 1
        
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        GLOBAL_CACHE_REGISTRY['original_name'] = orig_name
        return jsonify({"files": manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except: return "UI Missing", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)