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

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

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

def parse_raw_astc_header(data: bytes):
    if len(data) < 16 or data[:4] != b'\x13\xab\xa1\\':
        raise Exception("Malformed ASTC profile token")
    block_width = data[4]
    block_height = data[5]
    width = data[7] | (data[8] << 8) | (data[9] << 16)
    height = data[10] | (data[11] << 8) | (data[12] << 16)
    return width, height, block_width, block_height

def fetch_single_cdn_stream(url: str) -> bytes:
    headers = {"User-Agent": "ff-astc-api/1.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404: return b""
        resp.raise_for_status()
        return resp.content
    except:
        return b""

@app.route('/api/fetch_astc', methods=['GET'])
def handle_remote_astc_reconstruction():
    asset_id = request.args.get('id', '').strip()
    environment = request.args.get('env', 'live').strip().lower()

    if not asset_id or not re.match(r'^\d+$', asset_id):
        return jsonify({"error": "Bad request format parameters"}), 400

    base_url = f"https://dl-tata.freefireind.in/{environment}/ABHotUpdates/IconCDN/android"
    rgb_url = f"{base_url}/{asset_id}_rgb.astc"
    sa_url = f"{base_url}/{asset_id}_sa.astc"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_rgb = executor.submit(fetch_single_cdn_stream, rgb_url)
        future_sa = executor.submit(fetch_single_cdn_stream, sa_url)
        rgb_bytes = future_rgb.result()
        sa_bytes = future_sa.result()

    if not rgb_bytes or not sa_bytes:
        return jsonify({"error": f"Asset target ID {asset_id} not found on CDN clusters"}), 404

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
        return jsonify({"error": f"Decoding runtime tracking error: {str(error_log)}"}), 500

@app.route('/api/extract', methods=['POST'])
def handle_extraction():
    if 'asset_bundle' not in request.files: 
        return jsonify({"error": "No file payload detected"}), 400
    
    try:
        uploaded_file = request.files['asset_bundle']
        uploaded_file.seek(0, os.SEEK_END)
        file_length = uploaded_file.tell()
        uploaded_file.seek(0)

        if file_length > MAX_FILE_SIZE:
            return jsonify({"error": "File exceeds the maximum 5MB limits."}), 400

        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)
        
        memory_zip = io.BytesIO()
        json_manifest = []
        
        with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zip_archive:
            # Fallback direct data unpack processing for raw archives
            if b'\xABKTX 11\xBB\r\n\x1A\n' in decompressed_data:
                match = decompressed_data.find(b'\xABKTX 11\xBB\r\n\x1A\n')
                ktx_bytes = decompressed_data[match:]
                try:
                    f = io.BytesIO(ktx_bytes)
                    header = f.read(64)
                    gl_format = struct.unpack('<I', header[28:32])[0]
                    width = struct.unpack('<I', header[36:40])[0]
                    height = struct.unpack('<I', header[40:44])[0]
                    kv_len = struct.unpack('<I', header[60:64])[0]
                    f.seek(64 + kv_len)
                    img_size = struct.unpack('<I', f.read(4))[0]
                    img_data = f.read(img_size)
                    
                    if gl_format == 0x8D64:
                        decoded = texture2ddecoder.decode_etc1(img_data, width, height)
                    elif 0x93B0 <= gl_format <= 0x93BD:
                        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
                        bx, by = astc_formats[gl_format]
                        decoded = texture2ddecoder.decode_astc(img_data, width, height, bx, by)
                    else:
                        decoded = img_data[:width*height*4]
                        
                    img = Image.frombytes("RGBA", (width, height), decoded)
                    img = Image.merge("RGBA", (img.split()[2], img.split()[1], img.split()[0], img.split()[3])).transpose(Image.FLIP_TOP_BOTTOM)
                    
                    png_buf = io.BytesIO()
                    img.save(png_buf, format="PNG")
                    name = f"extracted_texture_{hashlib.md5(ktx_bytes).hexdigest()[:6]}.png"
                    target_path = f"Textures/{name}"
                    
                    zip_archive.writestr(target_path, png_buf.getvalue())
                    json_manifest.append({'name': name, 'path': target_path, 'label': "2D Texture"})
                except: pass
            
            # Extract plain text configurations safely using regular expressions
            text_strings = re.findall(b'[a-zA-Z0-9_\-\.\s\{\}\[\]\:\",]{10,}', decompressed_data)
            if text_strings:
                combined_text = b"\n".join([s.strip() for s in text_strings if len(s.strip()) > 15])
                if len(combined_text) > 50:
                    text_name = "configuration_manifest.txt"
                    zip_archive.writestr(f"Text/{text_name}", combined_text)
                    json_manifest.append({'name': text_name, 'path': f"Text/{text_name}", 'label': "Text"})

            zip_archive.writestr("manifest.json", json.dumps(json_manifest))

        if not json_manifest:
            return jsonify({"error": "No web-compatible asset components could be parsed."}), 400
        
        memory_zip.seek(0)
        return send_file(
            memory_zip,
            mimetype='application/zip',
            as_attachment=True,
            download_name='extracted_assets.zip'
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: 
            return f.read()
    except Exception as e: 
        return f"File Sync Error: {str(e)}", 500