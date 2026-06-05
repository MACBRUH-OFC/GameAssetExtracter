import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, send_file, jsonify

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy
import lz4.frame

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

# In-Memory Cache Store to hold elements safely inside serverless executions
GLOBAL_RAM_CACHE_MANIFEST = {}

def decompress_stream(data: bytes) -> bytes:
    """Deep brute-force compression layer stripping."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith(b'\x04\x22\x4d\x18'): return decompress_stream(lz4.frame.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def extract_pristine_name(obj, data, default_type: str) -> str:
    """Resolves exact structural asset name keys natively without mutations."""
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]

    name = getattr(data, "name", "")
    if isinstance(name, str) and name.strip():
        return name.strip()

    for attr in ["m_Name", "m_Container", "m_PathID"]:
        val = getattr(data, attr, None)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return f"{default_type}_{obj.path_id}"

def process_object_unrestricted(obj, raw_env_data: bytes):
    """Parses structural assets keeping exact internal built file naming maps."""
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_pristine_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode()
            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}"

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png"

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
                    raw = raw_env_data[start_pos:start_pos + 45_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4"

        elif t == "Mesh":
            lines = [f"g {safe_name}", "# Studio Core Asset v4.4"]
            if hasattr(data, 'm_Vertices'):
                for v in data.m_Vertices: lines.append(f"v {v.x} {v.y} {v.z}")
            if hasattr(data, 'm_Indices'):
                idx = data.m_Indices
                for i in range(0, len(idx), 3): lines.append(f"f {idx[i]+1} {idx[i+1]+1} {idx[i+2]+1}")
            return f"{safe_name}.obj", "\n".join(lines).encode(), f"Models/{safe_name}.obj"
            
    except Exception:
        pass
    return None

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    if path in ["api/extract", "api/extract/"] and request.method == "POST":
        return "Route handling POST payload context inside specific controllers.", 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Dashboard configuration read error: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def process_upload_pipeline():
    global GLOBAL_RAM_CACHE_MANIFEST
    
    # URL parameters control alternative download tracks
    download_type = request.args.get('download_type', '')

    # --- TRACK B: Zip Multi-Download Trigger ---
    if download_type == 'zip':
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted'):
            return jsonify({"error": "Cache layer cleared. Please re-upload stream."}), 400
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_RAM_CACHE_MANIFEST['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="studio_manifest_assets.zip")

    # --- TRACK C: Single Selective Asset Download Trigger ---
    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_RAM_CACHE_MANIFEST['extracted']):
            return jsonify({"error": "Target resource tracking index identifier missing."}), 400
        
        target_file_element = GLOBAL_RAM_CACHE_MANIFEST['extracted'][file_idx]
        return send_file(
            io.BytesIO(target_file_element['bytes']),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=target_file_element['name']
        )

    # --- TRACK A: Standard Asset Mapping Manifest Generation Pipeline ---
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file stream discovered in payload parameters."}), 400
            
        uploaded_file = request.files['file']
        raw_bytes = uploaded_file.read()

        if not raw_bytes:
            return jsonify({"error": "Payload byte sequence is empty."}), 400

        final_data = decompress_stream(bytes(raw_bytes))
        env = UnityPy.load(final_data)
        
        seen_md5 = set()
        extracted_list = []
        json_metadata_manifest = []
        tracking_index_counter = 0

        for obj in env.objects:
            res = process_object_unrestricted(obj, final_data)
            if res:
                filename, file_bytes, zip_folder_path = res
                h = hashlib.md5(file_bytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    
                    # Store variables into secure RAM mapping arrays
                    extracted_list.append({
                        'name': filename,
                        'zip_path': zip_folder_path,
                        'bytes': file_bytes
                    })
                    
                    # Store references to send back to client dashboard interface components
                    json_metadata_manifest.append({
                        'index': tracking_index_counter,
                        'name': filename,
                        'path': zip_folder_path
                    })
                    tracking_index_counter += 1

        if tracking_index_counter == 0:
            return jsonify({"error": "No valid resource parameters discovered inside stream headers."}), 400

        # Cache variables inside runtime layers
        GLOBAL_RAM_CACHE_MANIFEST['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})

    except Exception as e:
        return jsonify({"error": f"Internal mapping failure: {str(e)}"}), 500