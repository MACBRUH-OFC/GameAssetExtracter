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
import concurrent.futures
import requests
from flask import Flask, request, send_file, jsonify
from PIL import Image
import texture2ddecoder

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Strict 5MB file cap matching deployment platform limits
MAX_FILE_SIZE = 5 * 1024 * 1024 

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
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']:
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
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or raw.startswith(b"\n") or b"size:" in raw:
                if not safe_name.lower().endswith('.atlas'): ext = ".atlas.txt"
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", f"{t} Asset"

        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_atlas.json", js_bytes, f"Mapping/{safe_name}_atlas.json", "SpriteAtlas Map"

        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 15_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip"

        elif t == "Mesh":
            try:
                mesh_data = data.export().encode('utf-8')
                return f"{safe_name}.obj", mesh_data, f"Meshes/{safe_name}.obj", "3D Mesh"
            except:
                tree_data = dump_obj_to_dict(data)
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_mesh.json", js_bytes, f"Geometry/Mesh/{safe_name}.json", "Mesh Schema"

        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject", "SkinnedMeshRenderer", "MeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            folder = "Hierarchy" if t == "GameObject" else "Scripts" if "Script" in t else "Geometry"
            return f"{safe_name}_{t}.json", js_bytes, f"{folder}/{t}/{safe_name}.json", f"{t} Schema"

        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Shaders_Materials/{t}/{safe_name}.json", f"{t} Config"

        elif t in ["AnimationClip", "AnimatorController", "Animator"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Animations/{t}/{safe_name}.json", "Animation Map"

        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".otf" if raw_font_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font File"
            return f"{safe_name}_font.json", json.dumps(dump_obj_to_dict(data)).encode('utf-8'), f"Fonts/{safe_name}.json", "Font Metadata"

        elif t == "AssetBundle":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_manifest.json", js_bytes, f"Containers/{safe_name}.json", "Bundle Manifest"

        else:
            try:
                tree_data = dump_obj_to_dict(data)
                if tree_data:
                    js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                    return f"{safe_name}_{t}.json", js_bytes, f"Other/{t}/{safe_name}.json", f"Data ({t})"
            except: pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}_{t}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", f"Binary ({t})"
    except: pass
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n': raise Exception("Invalid KTX")
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    bytes_of_kv = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + bytes_of_kv)
    image_size = struct.unpack('<I', f.read(4))[0]
    data = f.read(image_size)
    if gl_internal_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    elif gl_internal_format == 0x8058: decoded = data[:width*height*4]
    else: raise Exception("Unsupported KTX")
    img = Image.frombytes("RGBA", (width, height), decoded)
    img = Image.merge("RGBA", (img.split()[2], img.split()[1], img.split()[0], img.split()[3])).transpose(Image.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()

def parse_raw_astc_header(data: bytes):
    if len(data) < 16 or data[:4] != b'\x13\xab\xa1\\':
        raise Exception("Malformed or unexpected ASTC file signature identifier")
    block_width = data[4]
    block_height = data[5]
    width = data[7] | (data[8] << 8) | (data[9] << 16)
    height = data[10] | (data[11] << 8) | (data[12] << 16)
    return width, height, block_width, block_height

def fetch_single_cdn_stream(url: str) -> bytes:
    headers = {"User-Agent": "ff-astc-api/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 404:
            return b""
        resp.raise_for_status()
        return resp.content
    except:
        return b""

@app.route('/api/fetch_astc', methods=['GET'])
def handle_remote_astc_reconstruction():
    asset_id = request.args.get('id', '').strip()
    environment = request.args.get('env', 'live').strip().lower()

    if not asset_id or not re.match(r'^\d+$', asset_id):
        return jsonify({"error": "Bad request: identification target must contain digits only"}), 400

    base_url = f"https://dl-tata.freefireind.in/{environment}/ABHotUpdates/IconCDN/android"
    rgb_url = f"{base_url}/{asset_id}_rgb.astc"
    sa_url = f"{base_url}/{asset_id}_sa.astc"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_rgb = executor.submit(fetch_single_cdn_stream, rgb_url)
        future_sa = executor.submit(fetch_single_cdn_stream, sa_url)
        rgb_bytes = future_rgb.result()
        sa_bytes = future_sa.result()

    if not rgb_bytes or not sa_bytes:
        return jsonify({"error": f"Asset target ID {asset_id} not found on server storage partition"}), 404

    try:
        w, h, bw, bh = parse_raw_astc_header(rgb_bytes)
        sa_w, sa_h, sa_bw, sa_bh = parse_raw_astc_header(sa_bytes)

        decoded_rgb = texture2ddecoder.decode_astc(rgb_bytes[16:], w, h, bw, bh)
        decoded_sa = texture2ddecoder.decode_astc(sa_bytes[16:], sa_w, sa_h, sa_bw, sa_bh)

        img_rgb = Image.frombytes("RGBA", (w, h), decoded_rgb)
        img_sa = Image.frombytes("RGBA", (sa_w, sa_h), decoded_sa)

        r, g, b, _ = img_rgb.split()
        alpha_channel, _, _, _ = img_sa.split()
        
        final_transparent_image = Image.merge("RGBA", (r, g, b, alpha_channel))

        output_buffer = io.BytesIO()
        final_transparent_image.save(output_buffer, format="PNG")
        output_buffer.seek(0)

        return send_file(
            output_buffer,
            mimetype='image/png',
            as_attachment=True,
            download_name=f"{asset_id}.png"
        )
    except Exception as error_log:
        return jsonify({"error": f"Decoding runtime malfunction: {str(error_log)}"}), 500

@app.route('/api/extract', methods=['POST'])
def handle_extraction():
    if 'asset_bundle' not in request.files: return jsonify({"error": "No file payload found"}), 400
    
    try:
        uploaded_file = request.files['asset_bundle']
        uploaded_file.seek(0, os.SEEK_END)
        file_length = uploaded_file.tell()
        uploaded_file.seek(0)

        if file_length > MAX_FILE_SIZE:
            return jsonify({"error": f"File exceeds 5MB platform limits."}), 400

        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)
        json_manifest = []
        
        if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            try:
                png_bytes = convert_ktx_to_png_fallback(decompressed_data)
                name = os.path.splitext(uploaded_file.filename)[0] + ".png"
                json_manifest.append({
                    'index': 0, 
                    'name': name, 
                    'path': f"Textures/{name}", 
                    'label': "KTX Image",
                    'hex_data': png_bytes.hex()
                })
                return jsonify({"files": json_manifest})
            except: pass

        env = UnityPy.load(decompressed_data)
        counter = 0
        seen_md5 = set()
        for obj in env.objects:
            res = process_object_unrestricted(obj, decompressed_data)
            if res:
                fname, fbytes, zpath, tlabel = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    json_manifest.append({
                        'index': counter, 
                        'name': fname, 
                        'path': zpath, 
                        'label': tlabel,
                        'hex_data': fbytes.hex()
                    })
                    counter += 1
        
        del env
        gc.collect()
        if not json_manifest: return jsonify({"error": "No valid assets found in payload"}), 400
        return jsonify({"files": json_manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        # Resolves runtime folder mapping reliably in local development and live production containers
        target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
        with open(target_path, 'r', encoding='utf-8') as f: 
            return f.read()
    except Exception as e: 
        return f"File Synchronization Error: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)