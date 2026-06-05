import os
import io
import json
import gzip
import zlib
import zipfile
import re
import gc
import hashlib
from flask import Flask, request, send_file, jsonify

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

# Core Application Global Memory Caches
CHUNKS_MEMORY_VAULT = {}
GLOBAL_RAM_CACHE_MANIFEST = {}

def decompress_stream(data: bytes) -> bytes:
    """Unpacks brute compression headers from underlying files safely."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def extract_pristine_name(obj, data, default_type: str) -> str:
    """Extracts internal binary manifest file naming tags."""
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]
    name = getattr(data, "name", "")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"{default_type}_{obj.path_id}"

def process_object_unrestricted(obj, raw_env_data: bytes):
    """Parses binary blocks keeping direct structure paths intact."""
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
        return f"Interface layout read error: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def process_upload_pipeline():
    global GLOBAL_RAM_CACHE_MANIFEST, CHUNKS_MEMORY_VAULT
    
    action = request.args.get('action', '')
    download_type = request.args.get('download_type', '')

    # --- ZIP MULTI-DOWNLOAD ENDPOINT ---
    if download_type == 'zip':
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted'):
            return jsonify({"error": "Cache layer empty. Please process a valid bundle file."}), 400
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_RAM_CACHE_MANIFEST['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="assets_manifest.zip")

    # --- SINGLE LIVE VIEW / DOWNLOAD PREVIEW ENDPOINT ---
    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_RAM_CACHE_MANIFEST['extracted']):
            return jsonify({"error": "Tracking item index identifier missing."}), 400
        
        item = GLOBAL_RAM_CACHE_MANIFEST['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    # --- INCOMING SEGMENT CHUNK HOOK ---
    if action == 'upload_chunk':
        try:
            session_id = request.form['session_id']
            chunk_index = int(request.form['chunk_index'])
            total_chunks = int(request.form['total_chunks'])
            slice_blob = request.files['slice_blob'].read()

            if session_id not in CHUNKS_MEMORY_VAULT:
                CHUNKS_MEMORY_VAULT[session_id] = [None] * total_chunks

            CHUNKS_MEMORY_VAULT[session_id][chunk_index] = slice_blob
            return "Block segment cached safely.", 200
        except Exception as e:
            return f"Segment chunk mapping error: {str(e)}", 500

    # --- FINAL RECONSTRUCTION & METADATA BINDING ENGINE ---
    elif action == 'finalize_assembly':
        try:
            session_id = request.args.get('session_id', '')
            if session_id not in CHUNKS_MEMORY_VAULT or None in CHUNKS_MEMORY_VAULT[session_id]:
                return jsonify({"error": "Slices missing inside delivery maps."}), 400

            full_reconstructed_bytes = b"".join(CHUNKS_MEMORY_VAULT[session_id])
            del CHUNKS_MEMORY_VAULT[session_id]

            final_data = decompress_stream(full_reconstructed_bytes)
            
            try:
                env = UnityPy.load(final_data)
                objects_array = env.objects
            except Exception:
                return jsonify({"error": "No structural game data found within asset headers."}), 400
            
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
                        
                        extracted_list.append({
                            'name': filename,
                            'zip_path': zip_folder_path,
                            'bytes': file_bytes
                        })
                        
                        json_metadata_manifest.append({
                            'index': tracking_index_counter,
                            'name': filename,
                            'path': zip_folder_path
                        })
                        tracking_index_counter += 1

            # Garbage collect to instantly release memory back to Vercel host
            del env
            gc.collect()

            if tracking_index_counter == 0:
                return jsonify({"error": "No unpackable asset classes detected inside file headers."}), 400

            GLOBAL_RAM_CACHE_MANIFEST['extracted'] = extracted_list
            return jsonify({"files": json_metadata_manifest})

        except Exception as e:
            return jsonify({"error": f"Internal pipeline compilation abort: {str(e)}"}), 500

    return jsonify({"error": "Invalid strategy execution pathway path."}), 400