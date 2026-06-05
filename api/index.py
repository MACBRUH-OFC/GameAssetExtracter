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

# Shared Persistent Volatile Storage Container Reference Map
GLOBAL_RAM_CACHE_MANIFEST = {}

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    if path in ["api/extract", "api/extract/"] and request.method == "POST":
        return "POST payload handling inside direct extraction route.", 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Interface layout file read error: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def process_upload_pipeline():
    global GLOBAL_RAM_CACHE_MANIFEST
    
    download_type = request.args.get('download_type', '')

    # --- ZIP MANIFEST GENERATION PORT ---
    if download_type == 'zip':
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted'):
            return jsonify({"error": "Cache data layer empty. Please re-upload your file."}), 400
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_RAM_CACHE_MANIFEST['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    # --- SINGLE LIVE PREVIEW LOOKUP ELEMENT ---
    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_RAM_CACHE_MANIFEST.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_RAM_CACHE_MANIFEST['extracted']):
            return jsonify({"error": "Target element index pointer error."}), 400
        
        item = GLOBAL_RAM_CACHE_MANIFEST['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    # --- FLAT STRAIGHTFORWARD EXTRACTION PIPELINE ---
    if 'bundle_file' not in request.files:
        return jsonify({"error": "No file container found inside request body."}), 400
        
    try:
        raw_uploaded_bytes = request.files['bundle_file'].read()

        # Brute decompression stripper block inside local stack scope
        try:
            if raw_uploaded_bytes.startswith(b'\x1f\x8b'):
                raw_uploaded_bytes = gzip.decompress(raw_uploaded_bytes)
            elif raw_uploaded_bytes.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
                raw_uploaded_bytes = zlib.decompress(raw_uploaded_bytes)
        except:
            pass

        try:
            env = UnityPy.load(raw_uploaded_bytes)
        except Exception:
            return jsonify({"error": "Invalid file signature header structure detected."}), 400

        seen_md5 = set()
        extracted_list = []
        json_metadata_manifest = []
        tracking_counter = 0

        # High performance flat iteration loop - avoids local function overhead variable references
        for obj in env.objects:
            try:
                t = obj.type.name
                if t not in ["TextAsset", "Texture2D", "Sprite", "AudioClip", "VideoClip"]:
                    continue

                data = obj.read()
                
                # Naming assignment normalization checks
                pristine_name = ""
                if hasattr(obj, 'container') and obj.container:
                    base_mapped_path = os.path.basename(obj.container)
                    if base_mapped_path:
                        pristine_name = os.path.splitext(base_mapped_path)[0]
                if not pristine_name:
                    name_attr = getattr(data, "name", "")
                    if isinstance(name_attr, str) and name_attr.strip():
                        pristine_name = name_attr.strip()
                if not pristine_name:
                    pristine_name = f"{t}_{obj.path_id}"

                safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)
                filename, file_bytes, zip_folder_path = None, None, None

                # Type categorization processing trees
                if t == "TextAsset":
                    raw = getattr(data, "m_Script", b"")
                    if isinstance(raw, str): 
                        raw = raw.encode()
                    ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
                    filename = f"{safe_name}{ext}"
                    file_bytes = raw
                    zip_folder_path = f"Text/{filename}"

                elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
                    buf = io.BytesIO()
                    data.image.save(buf, format="PNG", optimize=False)
                    file_bytes = buf.getvalue()
                    buf.close()
                    filename = f"{safe_name}.png"
                    zip_folder_path = f"Textures/{filename}"

                elif t == "AudioClip":
                    samples = getattr(data, "samples", None)
                    if samples and list(samples.keys()):
                        aud_name = list(samples.keys())[0]
                        filename = aud_name
                        file_bytes = samples[aud_name]
                    else:
                        raw = obj.get_raw_data()
                        ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
                        filename = f"{safe_name}{ext}"
                        file_bytes = raw
                    zip_folder_path = f"Audio/{filename}"

                elif t == "VideoClip":
                    raw = obj.get_raw_data()
                    if len(raw) < 1024:
                        match = raw_uploaded_bytes.find(b'ftyp')
                        if match != -1:
                            raw = raw_uploaded_bytes[max(0, match - 4):max(0, match - 4) + 12_000_000]
                    filename = f"{safe_name}.mp4"
                    file_bytes = raw
                    zip_folder_path = f"Video/{filename}"

                if file_bytes:
                    h = hashlib.md5(file_bytes).hexdigest()
                    if h not in seen_md5:
                        seen_md5.add(h)
                        
                        extracted_list.append({
                            'name': filename,
                            'zip_path': zip_folder_path,
                            'bytes': file_bytes
                        })
                        
                        json_metadata_manifest.append({
                            'index': tracking_counter,
                            'name': filename,
                            'path': zip_folder_path
                        })
                        tracking_counter += 1

            except Exception:
                pass # Gracefully skip broken internal components

        # Aggressive scope scrubbing to drop binary arrays immediately out of memory
        del env
        del raw_uploaded_bytes
        gc.collect()

        if tracking_counter == 0:
            return jsonify({"error": "No packable files found inside this bundle."}), 400

        GLOBAL_RAM_CACHE_MANIFEST['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})

    except Exception as e:
        return jsonify({"error": f"Internal unpacking failure: {str(e)}"}), 500