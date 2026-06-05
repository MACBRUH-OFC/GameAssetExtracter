import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, send_file, jsonify

# Prevent UnityPy from attempting to initialize any desktop GUI windows
os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy
import lz4.frame

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

def decompress_stream(data: bytes) -> bytes:
    """Deep brute-force compression stripping layer."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith(b'\x04\x22\x4d\x18'): return decompress_stream(lz4.frame.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def carve_raw_video(raw_env_data: bytes) -> bytes:
    """Low-level byte scanner to carve uncorrupted raw mp4 stream data blocks."""
    match = raw_env_data.find(b'ftyp')
    if match != -1:
        start_pos = max(0, match - 4)
        return raw_env_data[start_pos:start_pos + 45_000_000]
    return b""

def extract_pristine_name(obj, data, default_type: str) -> str:
    """
    Advanced Name Extraction: Resolves the exact built-in game asset identifier.
    Prioritizes raw internal object mappings over fallback naming arrays.
    """
    # 1. Check for standard structural container paths first
    if hasattr(obj, 'container') and obj.container:
        # Extracts 'shared_asset_name' out of 'assets/resources/shared_asset_name.png'
        extracted_path_name = os.path.basename(obj.container)
        if extracted_path_name:
            return os.path.splitext(extracted_path_name)[0]

    # 2. Extract standard internal engine string name property
    name = getattr(data, "name", "")
    if isinstance(name, str) and name.strip():
        return name.strip()

    # 3. Check for specific internal asset descriptor structures
    for attr in ["m_Name", "m_Container", "m_PathID"]:
        val = getattr(data, attr, None)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # 4. Strict structural fallback tracking
    return f"{default_type}_{obj.path_id}"

def process_object_unrestricted(obj, raw_env_data: bytes):
    """
    Upgraded extraction pipeline maintaining pristine, un-renamed 
    in-game built resource file nomenclature.
    """
    try:
        t = obj.type.name
        data = obj.read()
        
        # Isolate pristine internal identity name without mutations
        pristine_name = extract_pristine_name(obj, data, t)
        
        # Sanitize OS specific path hazards exclusively while maintaining literal name strings
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        # 1. Text Assets / Configurations
        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode()
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"Text/{safe_name}{ext}", raw

        # 2. High-Fidelity Textures & Visual Sprites
        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"Textures/{safe_name}.png", buf.getvalue()

        # 3. Audio Asset Infrastructure (PCM/Vorbis Rebuilding fix)
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples:
                # Extracts raw wave data dictionaries directly from memory frames
                audio_filenames = list(samples.keys())
                if audio_filenames:
                    return f"Audio/{audio_filenames[0]}", samples[audio_filenames[0]]
            
            # Binary byte dump array recovery fallback
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"Audio/{safe_name}{ext}", raw

        # 4. Unrestricted Video Streams Carving
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            # Handle hidden streaming pointer structures
            if len(raw) < 1024:
                carved_video = carve_raw_video(raw_env_data)
                if carved_video:
                    raw = carved_video
            return f"Video/{safe_name}.mp4", raw

        # 5. 3D Meshes Model Coordinate Export
        elif t == "Mesh":
            lines = [f"g {safe_name}", "# Enterprise Asset Recovery Struct Node v4.3"]
            if hasattr(data, 'm_Vertices'):
                for v in data.m_Vertices: lines.append(f"v {v.x} {v.y} {v.z}")
            if hasattr(data, 'm_Indices'):
                idx = data.m_Indices
                for i in range(0, len(idx), 3): lines.append(f"f {idx[i]+1} {idx[i+1]+1} {idx[i+2]+1}")
            return f"Models/{safe_name}.obj", "\n".join(lines).encode()
            
    except Exception:
        pass
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
        return f"Unable to fetch web dashboard layers: {str(e)}", 500

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
                    # Eliminate duplicated internal structures safely using MD5 hashes
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