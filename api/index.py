import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, send_file, jsonify

# Prevent UnityPy from attempting to initialize any desktop GUI systems
os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy
import lz4.frame

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

def decompress_stream(data: bytes) -> bytes:
    """Deep brute-force block container decompressor layers."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith(b'\x04\x22\x4d\x18'): return decompress_stream(lz4.frame.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def carve_raw_video(raw_env_data: bytes, path_id: int) -> bytes:
    """
    Implements a low-level byte carver mimicking a subprocess stream scan.
    Locates original mp4/ftyp container markers directly out of the raw binary mapping.
    """
    # Look for standard high-definition video container file signatures (ftypmp42, ftypisom, etc.)
    match = raw_env_data.find(b'ftyp')
    if match != -1:
        # Step back 4 bytes to catch the size header prefix of the video container atom
        start_pos = max(0, match - 4)
        # Extract up to 45MB of the continuous media stream block dynamically
        return raw_env_data[start_pos:start_pos + 45_000_000]
    return b""

def process_object_unrestricted(obj, raw_env_data: bytes):
    """
    Upgraded extraction layout maximizing asset recovery while strictly
    preserving internal native original in-game built resource file names.
    """
    try:
        t = obj.type.name
        data = obj.read()
        
        # STRICT REQUIREMENT: Extract and isolate the original internal built asset name
        name = getattr(data, "name", "").strip()
        if not name:
            # Fallback only if the in-game engine assigned an empty string tag
            name = f"{t}_{obj.path_id}"
            
        # Clean operating system forbidden path characters, keeping original names pristine
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

        # 1. Text / Variables Extraction
        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode()
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"Text/{name}{ext}", raw

        # 2. Texture & UI Sprite Asset Recovery
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"Textures/{name}.png", buf.getvalue()

        # 3. Audio Asset Recovery (Fixes unplayable tracks)
        elif t == "AudioClip":
            # Extract sample data tracking layers accurately via UnityPy helper extensions
            samples = data.samples
            if samples:
                # Rebuild and wrap the dictionary tracking blocks back into valid media files
                audio_name = list(samples.keys())[0]
                return f"Audio/{audio_name}", samples[audio_name]
            
            # Subprocess-style byte array carving fallback for legacy audio containers
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"Audio/{name}{ext}", raw

        # 4. Deep Video Carving (Fixes unviewable videos)
        elif t == "VideoClip":
            # Pull direct raw clip pointers
            raw = obj.get_raw_data()
            # If the pointer is hollow or small (< 1KB), deploy our binary stream carver
            if len(raw) < 1024:
                carved_video = carve_raw_video(raw_env_data, obj.path_id)
                if carved_video:
                    raw = carved_video
            return f"Video/{name}.mp4", raw

        # 5. 3D Model Coordinate Export
        elif t == "Mesh":
            lines = [f"g {name}", "# Native Structural Recovery Pipeline v4.2"]
            if hasattr(data, 'm_Vertices'):
                for v in data.m_Vertices: lines.append(f"v {v.x} {v.y} {v.z}")
            if hasattr(data, 'm_Indices'):
                idx = data.m_Indices
                for i in range(0, len(idx), 3): lines.append(f"f {idx[i]+1} {idx[i+1]+1} {idx[i+2]+1}")
            return f"Models/{name}.obj", "\n".join(lines).encode()
            
    except Exception:
        pass  # Gracefully fall through on unhandled system assets
    return None

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    if path in ["api/extract", "api/extract/"]:
        return jsonify({"error": "Method execution blocked."}), 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Dashboard missing or unreadable: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def process_upload_pipeline():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No valid file stream container found."}), 400
            
        uploaded_file = request.files['file']
        raw_bytes = uploaded_file.read()

        if not raw_bytes or len(raw_bytes) == 0:
            return jsonify({"error": "The uploaded payload contains an empty sequence."}), 400

        # Execute Deep Stream Reconstruction
        final_data = decompress_stream(bytes(raw_bytes))
        env = UnityPy.load(final_data)
        
        zip_io = io.BytesIO()
        seen = set()
        extracted_count = 0

        # Build production delivery container using maximum speed configuration
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for obj in env.objects:
                res = process_object_unrestricted(obj, final_data)
                if res:
                    path, data = res
                    # Generate hashing checksums to eliminate layout duplicates cleanly
                    h = hashlib.md5(data).hexdigest()
                    if h not in seen:
                        seen.add(h)
                        zf.writestr(path, data)
                        extracted_count += 1

        if extracted_count == 0:
            return jsonify({"error": "No valid assets could be recovered from this specific container cluster."}), 400

        zip_io.seek(0)
        return send_file(
            zip_io,
            mimetype='application/zip',
            as_attachment=True,
            download_name="recovered_original_assets.zip"
        )

    except Exception as e:
        return jsonify({"error": f"Internal system tracking fault: {str(e)}"}), 500