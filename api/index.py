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
import traceback
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
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
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

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}"

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            img_bytes = buf.getvalue()
            buf.close()
            return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png"

        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 12_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4"
    except:
        pass
    return None

def convert_ktx_to_png(file_bytes):
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64:
        raise Exception("Invalid KTX file")
    if header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n':
        raise Exception("Not a valid KTX file")

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
        astc_formats = {
            0x93B0: (4, 4), 0x93B1: (5, 4), 0x93B2: (5, 5), 0x93B3: (6, 5),
            0x93B4: (6, 6), 0x93B5: (8, 5), 0x93B6: (8, 6), 0x93B7: (8, 8),
            0x93B8: (10, 5), 0x93B9: (10, 6), 0x93BA: (10, 8), 0x93BB: (10, 10),
            0x93BC: (12, 10), 0x93BD: (12, 12)
        }
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    elif gl_internal_format == 0x8058:
        expected = width * height * 4
        if len(data) < expected:
            raise Exception(f"Texture data too small. Expected: {expected} Found: {len(data)}")
        decoded = data[:expected]
    else:
        raise Exception(f"Unsupported format: {hex(gl_internal_format)}")

    img = Image.frombytes("RGBA", (width, height), decoded)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a))
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    
    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

def convert_png_to_ktx(file_bytes):
    img = Image.open(io.BytesIO(file_bytes)).convert("RGBA")
    img = img.transpose(Image.FLIP_TOP_BOTTOM)
    r, g, b, a = img.split()
    img = Image.merge("RGBA", (b, g, r, a))
    width, height = img.size
    pixel_data = img.tobytes()

    kv_pair = b"KTXorientation\x00S=r,T=d\x00"
    kv_entry = struct.pack('<I', len(kv_pair)) + kv_pair
    padding = (4 - (len(kv_entry) % 4)) % 4
    kv_block = kv_entry + (b'\x00' * padding)

    header = struct.pack(
        '<12sIIIIIIIIIIII',
        b'\xABKTX 11\xBB\r\n\x1A\n',
        0x04030201, 0x1401, 1, 0x1908, 0x8058, 0x1908,
        width, height, 0, 0, 1, len(kv_block)
    )

    output = io.BytesIO()
    output.write(header)
    output.write(kv_block)
    output.write(struct.pack('<I', len(pixel_data)))
    output.write(pixel_data)
    output.seek(0)
    return output

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    if path in ["api/extract", "api/extract/", "api/convert", "api/convert/"] and request.method == "POST":
        return "POST stream pathways execute on specific backend routes exclusively.", 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Interface layout missing or broken: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def handle_direct_extraction_stream():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache registry empty. Re-stream source package container."}), 400
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Target mapping index reference lost."}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "Multipart byte payload context missing."}), 400

    try:
        raw_bundle_bytes = request.files['asset_bundle'].read()
        final_data = decompress_stream(raw_bundle_bytes)
        try:
            env = UnityPy.load(final_data)
            objects_array = env.objects
        except:
            return jsonify({"error": "Invalid format layout. Standard package headers not verified."}), 400

        seen_md5 = set()
        extracted_list = []
        json_metadata_manifest = []
        tracking_index_counter = 0

        for obj in objects_array:
            res = process_object_unrestricted(obj, final_data)
            if res:
                filename, file_bytes, zip_folder_path = res
                h = hashlib.md5(file_bytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    extracted_list.append({'name': filename, 'zip_path': zip_folder_path, 'bytes': file_bytes})
                    json_metadata_manifest.append({'index': tracking_index_counter, 'name': filename, 'path': zip_folder_path})
                    tracking_index_counter += 1
        del env
        gc.collect()

        if tracking_index_counter == 0:
            return jsonify({"error": "No valid supported structural elements recognized inside files."}), 400

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})
    except Exception as e:
        return jsonify({"error": f"Internal execution thread pipeline exception: {str(e)}"}), 500

@app.route("/api/convert", methods=["POST"])
def api_convert():
    try:
        mode = request.form.get("mode")
        file = request.files.get("file")
        if not file:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        file_bytes = file.read()
        if mode == "ktx_to_png":
            output = convert_ktx_to_png(file_bytes)
            return send_file(output, mimetype="image/png", as_attachment=True, download_name="converted.png")
        elif mode == "png_to_ktx":
            output = convert_png_to_ktx(file_bytes)
            return send_file(output, mimetype="application/octet-stream", as_attachment=True, download_name="converted.ktx")
        else:
            return jsonify({"success": False, "error": "Invalid mode"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)