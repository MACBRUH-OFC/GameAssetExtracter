import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, send_file, jsonify
import UnityPy
import lz4.frame

app = Flask(__name__)

def decompress_stream(data: bytes) -> bytes:
    """Brute-force decompression: strips compression layers."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith(b'\x04\x22\x4d\x18'): return decompress_stream(lz4.frame.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def process_object_unrestricted(obj, raw_env_data: bytes):
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
                if match != -1: raw = raw_env_data[match:match+30_000_000]
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

@app.route('/api/extract', methods=['POST'])
def extract_assets():
    try:
        # Check if a file was uploaded via multipart/form-data
        if 'file' not in request.files:
            return jsonify({"error": "No file stream detected in payload."}), 400
            
        uploaded_file = request.files['file']
        raw_bytes = uploaded_file.read()

        if not raw_bytes:
            return jsonify({"error": "Uploaded binary stream is empty."}), 400

        # Execute Core Engine Extraction
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
            return jsonify({"error": "No recognizable Unity assets found in file."}), 400

        # Reset stream position and send download back to client
        zip_io.seek(0)
        return send_file(
            zip_io,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"extracted_assets.zip"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Required for local testing or secondary routing fallback
if __name__ == '__main__':
    app.run(debug=True)