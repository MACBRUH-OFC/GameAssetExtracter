import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, send_file, jsonify

# Force UnityPy configuration environments to operate safely in serverless headers
os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy
import lz4.frame

app = Flask(__name__)

# Absolute asset pathing maps inside the Vercel Lambda deployment container
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

def decompress_stream(data: bytes) -> bytes:
    """Brute-force decompression engine matching the original bot.py specifications."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith(b'\x04\x22\x4d\x18'): return decompress_stream(lz4.frame.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def process_object_unrestricted(obj, raw_env_data: bytes):
    """Processes, filters, and formats extensionless files into asset trees."""
    try:
        t = obj.type.name
        data = obj.read()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", getattr(data, "name", f"{t}_{obj.path_id}"))

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode()
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"Text/{name}{ext}", raw

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"Textures/{name}.png", buf.getvalue()

        elif t == "AudioClip":
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"Audio/{name}{ext}", raw

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 500:
                match = raw_env_data.find(b'ftyp')
                if match != -1: raw = raw_env_data[match:match+30_000_000] # Kept memory safe for serverless thresholds
            return f"Video/{name}.mp4", raw

        elif t == "Mesh":
            lines = [f"g {name}", "# Unrestricted Web Engine v4.0"]
            if hasattr(data, 'm_Vertices'):
                for v in data.m_Vertices: lines.append(f"v {v.x} {v.y} {v.z}")
            if hasattr(data, 'm_Indices'):
                idx = data.m_Indices
                for i in range(0, len(idx), 3): lines.append(f"f {idx[i]+1} {idx[i+1]+1} {idx[i+2]+1}")
            return f"Models/{name}.obj", "\n".join(lines).encode()
    except: pass
    return None

# Combined router rendering frontend from memory directly to avoid routing 404s
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_application_cockpit(path):
    if path in ["api/extract", "api/extract/"]:
        return jsonify({"error": "Method not allowed. Execute POST request."}), 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"System failed to render cockpit console files: {str(e)}", 500

# Unified extractor execution route
@app.route('/api/extract', methods=['POST'])
def run_extraction_pipeline():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No data stream chunk discovered in the request multipart headers."}), 400
            
        uploaded_file = request.files['file']
        raw_bytes = uploaded_file.read()

        if not raw_bytes:
            return jsonify({"error": "The uploaded payload contains an empty byte sequence."}), 400

        # Execute Core Asset Recovery Stream
        final_data = decompress_stream(bytes(raw_bytes))
        env = UnityPy.load(final_data)
        
        zip_io = io.BytesIO()
        seen = set()
        extracted_count = 0

        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for obj in env.objects:
                res = process_object_unrestricted(obj, final_data)
                if res:
                    path, data = res
                    h = hashlib.md5(data).hexdigest()
                    if h not in seen:
                        seen.add(h)
                        zf.writestr(path, data)
                        extracted_count += 1

        if extracted_count == 0:
            return jsonify({"error": "No recognizable Unity structures found within the target file cluster."}), 400

        zip_io.seek(0)
        return send_file(
            zip_io,
            mimetype='application/zip',
            as_attachment=True,
            download_name="recovered_assets.zip"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500