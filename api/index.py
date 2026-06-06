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
        sb.append(f"# Exported from UnityPy\ng {getattr(data, 'name', 'Mesh')}")
        if hasattr(data, 'm_Vertices'):
            for i in range(0, len(data.m_Vertices), 3):
                v = data.m_Vertices[i:i+3]
                if len(v) == 3:
                    sb.append(f"v {v[0]} {v[1]} {v[2]}")
        if hasattr(data, 'm_Indices'):
            for i in range(0, len(data.m_Indices), 3):
                f = data.m_Indices[i:i+3]
                if len(f) == 3:
                    sb.append(f"f {f[0]+1} {f[1]+1} {f[2]+1}")
        return "\n".join(sb).encode('utf-8')
    except:
        return b""

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
            if safe_name.lower().endswith('.atlas') or raw.startswith(b"\n") or b"size:" in raw:
                ext = ".atlas"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
            return f"{safe_name}{ext}", raw, f"TextAssets/{safe_name}{ext}", label
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", t
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "AudioClip"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "AudioClip"
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Videos/{safe_name}.mp4", "VideoClip"
        elif t == "Mesh":
            obj_data = export_mesh_to_obj(data)
            if obj_data:
                return f"{safe_name}.obj", obj_data, f"Models/{safe_name}.obj", "Mesh"
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}_mesh.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Models/{safe_name}.json", "Mesh"
        elif t in ["Font", "TrueTypeFont"]:
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".otf" if raw_font_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font"
        elif t in ["Shader", "Material", "MonoBehaviour", "AnimatorController", "AnimationClip", "AssetBundle", "SkinnedMeshRenderer", "MeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            folder = f"{t}s"
            return f"{safe_name}.json", js_bytes, f"{folder}/{safe_name}.json", t
        else:
            try:
                tree_data = dump_obj_to_dict(data)
                if tree_data:
                    return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Other/{t}/{safe_name}.json", t
            except:
                pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", t
    except:
        pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n':
        raise Exception("Invalid KTX")
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    bytes_of_kv = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + bytes_of_kv)
    image_size = struct.unpack('<I', f.read(4))[0]
    data = f.read(image_size)
    if gl_internal_format == 0x8D64:
        decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    elif gl_internal_format == 0x8058:
        decoded = data[:width*height*4]
    else:
        raise Exception("Unsupported KTX Format")
    img = Image.frombytes("RGBA", (width, height), decoded)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a)).transpose(Image.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "No data"}), 400
        is_grouped = request.args.get('mode') == 'grouped'
        filter_list = request.args.get('filters', '').split(',')
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                if filter_list and filter_list[0] and item['label'] not in filter_list:
                    continue
                path = item['zip_path'] if is_grouped else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")
    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Index Error"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])
    if 'asset_bundle' not in request.files:
        return jsonify({"error": "Missing file"}), 400
    try:
        uploaded_file = request.files['asset_bundle']
        orig_name = os.path.basename(uploaded_file.filename)
        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)
        extracted_list = []
        json_metadata_manifest = []
        seen_md5 = set()
        if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            png = convert_ktx_to_png_fallback(decompressed_data)
            clean_name = os.path.splitext(orig_name)[0] + ".png"
            extracted_list.append({'name': clean_name, 'zip_path': f"Textures/{clean_name}", 'bytes': png, 'label': 'Texture2D'})
            json_metadata_manifest.append({'index': 0, 'name': clean_name, 'path': f"Textures/{clean_name}", 'label': 'Texture2D'})
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
                        json_metadata_manifest.append({'index': counter, 'name': fname, 'path': zpath, 'label': tlabel})
                        counter += 1
            del env
            gc.collect()
        if not extracted_list:
            return jsonify({"error": "No supported assets found"}), 400
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)