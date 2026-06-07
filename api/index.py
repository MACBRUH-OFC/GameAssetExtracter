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
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')

# Global cache for extracted assets between requests
GLOBAL_CACHE_REGISTRY = {}

# Supported asset types for extraction
SUPPORTED_TYPES = [
    "Texture2D", "Sprite", "AudioClip", "VideoClip", "TextAsset",
    "Font", "Mesh", "Shader", "MonoBehaviour", "GameObject",
    "Material", "AnimationClip", "AnimatorController", "Cubemap",
    "RenderTexture", "MovieTexture", "SpriteAtlas", "TerrainData"
]

# File size limits
MAX_BUNDLE_SIZE = 50 * 1024 * 1024  # 50MB max upload


def decompress_stream(data: bytes) -> bytes:
    """Recursively decompress various compression wrappers."""
    try:
        # GZip magic bytes
        if len(data) > 2 and data[0:2] == b'\x1f\x8b':
            return decompress_stream(gzip.decompress(data))
        # Zlib compression headers
        if len(data) > 2 and data[0] == 0x78 and (data[1] in [0x01, 0x9C, 0xDA]):
            return decompress_stream(zlib.decompress(data))
        # Unity LZ4 compression (simple detection)
        if len(data) > 4 and data[0:4] == b'\x04\x00\x00\x00':
            try:
                import lz4.block
                decompressed = lz4.block.decompress(data[4:])
                if decompressed:
                    return decompressed
            except ImportError:
                pass
    except Exception:
        pass
    return data


def extract_clean_name(obj, data, default_type: str, obj_index: int = 0) -> str:
    """Extract clean asset name from Unity object with fallbacks."""
    # Try to get container path name
    if hasattr(obj, 'container') and obj.container:
        base_path = os.path.basename(obj.container)
        if base_path and base_path.strip():
            name = os.path.splitext(base_path)[0]
            if name and not name.startswith('assets/'):
                return sanitize_filename(name)
    
    # Try common name attributes
    for attr in ["m_Name", "m_name", "name", "m_AssetBundleName"]:
        if hasattr(data, attr):
            val = getattr(data, attr, "")
            if val and isinstance(val, str) and val.strip():
                return sanitize_filename(val.strip())
    
    # For sprites, try to get texture name
    if default_type == "Sprite" and hasattr(data, 'm_Texture'):
        tex = getattr(data, 'm_Texture', None)
        if tex and hasattr(tex, 'm_Name'):
            return sanitize_filename(tex.m_Name or f"sprite_{obj.path_id}")
    
    # Fallback with type and ID
    return sanitize_filename(f"{default_type}_{obj.path_id}_{obj_index}")


def sanitize_filename(name: str) -> str:
    """Remove invalid filesystem characters."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip() or "unnamed"


def process_texture(data, obj, safe_name: str):
    """Extract texture as PNG."""
    try:
        if hasattr(data, 'image'):
            img_buffer = io.BytesIO()
            img = data.image
            if img:
                img.save(img_buffer, format="PNG", optimize=False)
                img_bytes = img_buffer.getvalue()
                img_buffer.close()
                return f"{safe_name}.png", img_bytes, f"Textures/{safe_name}.png"
    except Exception as e:
        print(f"Texture export error: {e}")
    return None


def process_audio_clip(data, obj, safe_name: str):
    """Extract audio clip - handles multiple formats."""
    try:
        # Try samples dictionary first (common in Unity bundles)
        if hasattr(data, 'samples') and data.samples:
            samples_dict = data.samples
            if samples_dict and isinstance(samples_dict, dict) and len(samples_dict):
                for audio_name, audio_data in samples_dict.items():
                    if audio_data and len(audio_data) > 100:
                        ext = determine_audio_format(audio_data)
                        final_name = sanitize_filename(audio_name) if audio_name else safe_name
                        return f"{final_name}{ext}", audio_data, f"Audio/{final_name}{ext}"
        
        # Try raw data
        raw_data = obj.get_raw_data()
        if raw_data and len(raw_data) > 1024:
            ext = determine_audio_format(raw_data)
            return f"{safe_name}{ext}", raw_data, f"Audio/{safe_name}{ext}"
        
        # Try m_AudioData attribute
        if hasattr(data, 'm_AudioData') and data.m_AudioData:
            audio_bytes = bytes(data.m_AudioData)
            if len(audio_bytes) > 1024:
                ext = determine_audio_format(audio_bytes)
                return f"{safe_name}{ext}", audio_bytes, f"Audio/{safe_name}{ext}"
    except Exception as e:
        print(f"AudioClip error: {e}")
    return None


def determine_audio_format(data: bytes) -> str:
    """Detect audio format from magic bytes."""
    if len(data) < 4:
        return ".bin"
    if data[0:4] == b'OggS':
        return ".ogg"
    if data[0:4] == b'RIFF':
        return ".wav"
    if data[0:3] == b'ID3':
        return ".mp3"
    if data[0:4] == b'ftyp':
        return ".m4a"
    return ".audio"


def process_video_clip(data, obj, safe_name: str, env_data: bytes):
    """Extract video clip - searches for MP4/WebM signatures."""
    try:
        raw = obj.get_raw_data()
        if raw and len(raw) > 4096:
            # Check for video signatures
            if b'moov' in raw or b'ftyp' in raw or b'MDAT' in raw:
                return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4"
        
        # Search entire bundle for video signatures
        video_start = -1
        signatures = [b'ftypmp4', b'ftypisom', b'moov', b'MDAT']
        for sig in signatures:
            pos = env_data.find(sig)
            if pos != -1 and pos < len(env_data):
                video_start = max(0, pos - 64)
                break
        
        if video_start != -1:
            # Extract reasonable chunk (up to 20MB)
            video_bytes = env_data[video_start:video_start + 20_000_000]
            return f"{safe_name}.mp4", video_bytes, f"Video/{safe_name}.mp4"
    except Exception as e:
        print(f"VideoClip error: {e}")
    return None


def process_text_asset(data, obj, safe_name: str):
    """Extract text asset as JSON or TXT."""
    try:
        raw_text = ""
        if hasattr(data, 'm_Script'):
            raw_text = data.m_Script
        elif hasattr(data, 'script'):
            raw_text = data.script
        
        if raw_text:
            if isinstance(raw_text, bytes):
                raw_text = raw_text.decode('utf-8', errors='replace')
            elif not isinstance(raw_text, str):
                raw_text = str(raw_text)
            
            # Determine if it's JSON-like
            stripped = raw_text.strip()
            if stripped.startswith(('{', '[')):
                ext = ".json"
            else:
                ext = ".txt"
            return f"{safe_name}{ext}", raw_text.encode('utf-8'), f"Text/{safe_name}{ext}"
    except Exception:
        pass
    return None


def process_font(data, obj, safe_name: str):
    """Extract font data (TTF/OTF)."""
    try:
        if hasattr(data, 'm_FontData') and data.m_FontData:
            font_bytes = bytes(data.m_FontData) if isinstance(data.m_FontData, (bytes, bytearray)) else data.m_FontData
            if font_bytes and len(font_bytes) > 100:
                # Check for TrueType signature
                if font_bytes[0:4] == b'\x00\x01\x00\x00' or font_bytes[0:4] == b'OTTO':
                    return f"{safe_name}.ttf", font_bytes, f"Fonts/{safe_name}.ttf"
                elif font_bytes[0:4] == b'wOFF':
                    return f"{safe_name}.woff", font_bytes, f"Fonts/{safe_name}.woff"
                else:
                    return f"{safe_name}.font", font_bytes, f"Fonts/{safe_name}.font"
    except Exception:
        pass
    return None


def process_mesh(data, obj, safe_name: str):
    """Extract mesh data as OBJ format (simplified)."""
    try:
        # For actual mesh geometry extraction would be complex
        # Export raw Unity mesh data as .mesh or .obj representation
        raw_data = obj.get_raw_data()
        if raw_data and len(raw_data) > 64:
            return f"{safe_name}.mesh", raw_data, f"Meshes/{safe_name}.mesh"
    except Exception:
        pass
    return None


def process_shader(data, obj, safe_name: str):
    """Extract shader source or raw bytes."""
    try:
        raw_data = obj.get_raw_data()
        if raw_data and len(raw_data) > 64:
            # Try to extract text if possible
            try:
                text = raw_data.decode('utf-8', errors='ignore')
                if 'Shader' in text or 'CGPROGRAM' in text:
                    return f"{safe_name}.shader", text.encode('utf-8'), f"Shaders/{safe_name}.shader"
            except:
                pass
            return f"{safe_name}.shader", raw_data, f"Shaders/{safe_name}.shader"
    except Exception:
        pass
    return None


def process_monobehaviour(data, obj, safe_name: str):
    """Extract MonoBehaviour script data as JSON."""
    try:
        script_data = {}
        if hasattr(data, 'm_Script'):
            script_data['script'] = str(data.m_Script)
        if hasattr(data, 'm_Name'):
            script_data['name'] = data.m_Name
        
        # Collect all serialized fields
        for attr in dir(data):
            if not attr.startswith('_') and not callable(getattr(data, attr)):
                try:
                    val = getattr(data, attr)
                    if val is not None and not isinstance(val, (type, type(UnityPy))):
                        script_data[attr] = str(val)[:500]
                except:
                    pass
        
        if script_data:
            json_bytes = json.dumps(script_data, indent=2).encode('utf-8')
            return f"{safe_name}.json", json_bytes, f"Scripts/{safe_name}.json"
    except Exception:
        pass
    return None


def process_object_unrestricted(obj, raw_env_data: bytes, index_counter: int):
    """Route asset to appropriate extraction handler."""
    try:
        obj_type = obj.type.name
        if obj_type not in SUPPORTED_TYPES:
            return None
        
        data = obj.read()
        base_name = extract_clean_name(obj, data, obj_type, index_counter)
        
        # Route by type
        if obj_type in ["Texture2D", "Sprite"]:
            return process_texture(data, obj, base_name)
        elif obj_type == "AudioClip":
            return process_audio_clip(data, obj, base_name)
        elif obj_type == "VideoClip":
            return process_video_clip(data, obj, base_name, raw_env_data)
        elif obj_type == "TextAsset":
            return process_text_asset(data, obj, base_name)
        elif obj_type == "Font":
            return process_font(data, obj, base_name)
        elif obj_type == "Mesh":
            return process_mesh(data, obj, base_name)
        elif obj_type == "Shader":
            return process_shader(data, obj, base_name)
        elif obj_type == "MonoBehaviour":
            return process_monobehaviour(data, obj, base_name)
        elif obj_type in ["Material", "AnimationClip", "GameObject"]:
            # Export as JSON representation
            try:
                obj_json = json.dumps({
                    "type": obj_type,
                    "name": base_name,
                    "path_id": obj.path_id
                }, indent=2).encode('utf-8')
                return f"{base_name}.json", obj_json, f"{obj_type}s/{base_name}.json"
            except:
                pass
    
    except Exception as e:
        # Silent fail for individual objects
        print(f"Error processing {obj.type.name}: {e}")
    
    return None


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    """Serve the main HTML interface."""
    if path and path.startswith('api/'):
        return jsonify({"error": "Invalid API endpoint"}), 404
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        # Return inline fallback if index.html missing
        return """
        <!DOCTYPE html>
        <html><head><title>Asset Extractor</title></head>
        <body><h1>Asset Extractor API</h1><p>Server is running. POST to /api/extract with asset_bundle file.</p></body>
        </html>
        """, 200
    except Exception as e:
        return f"Error loading interface: {str(e)}", 500


@app.route('/api/extract', methods=['POST', 'OPTIONS'])
def handle_extraction():
    """Main extraction endpoint - handles bundle parsing and asset extraction."""
    if request.method == 'OPTIONS':
        return '', 200
    
    global GLOBAL_CACHE_REGISTRY
    
    download_type = request.args.get('download_type', '')
    
    # Handle ZIP download of all cached assets
    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "No extracted assets in cache. Please upload a bundle first."}), 400
        
        zip_buffer = io.BytesIO()
        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for item in GLOBAL_CACHE_REGISTRY['extracted']:
                    zf.writestr(item['zip_path'], item['bytes'])
            zip_buffer.seek(0)
            return send_file(
                zip_buffer,
                mimetype='application/zip',
                as_attachment=True,
                download_name=f"extracted_assets_{hashlib.md5(str(GLOBAL_CACHE_REGISTRY.get('timestamp', '')).encode()).hexdigest()[:8]}.zip"
            )
        except Exception as e:
            return jsonify({"error": f"ZIP creation failed: {str(e)}"}), 500
    
    # Handle single asset download
    elif download_type == 'single':
        file_idx = request.args.get('file_index', type=int)
        if file_idx is None:
            return jsonify({"error": "Missing file_index parameter"}), 400
        
        extracted = GLOBAL_CACHE_REGISTRY.get('extracted', [])
        if file_idx < 0 or file_idx >= len(extracted):
            return jsonify({"error": "Asset index out of range"}), 400
        
        item = extracted[file_idx]
        mime_map = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.ogg': 'audio/ogg',
            '.wav': 'audio/wav',
            '.mp3': 'audio/mpeg',
            '.mp4': 'video/mp4',
            '.json': 'application/json',
            '.txt': 'text/plain',
            '.ttf': 'font/ttf',
            '.shader': 'text/plain',
            '.mesh': 'application/octet-stream'
        }
        ext = os.path.splitext(item['name'])[1].lower()
        mime = mime_map.get(ext, 'application/octet-stream')
        
        return send_file(
            io.BytesIO(item['bytes']),
            mimetype=mime,
            as_attachment=True,
            download_name=item['name']
        )
    
    # Main extraction flow
    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file provided. Use 'asset_bundle' field with your Unity bundle."}), 400
    
    file = request.files['asset_bundle']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400
    
    try:
        # Read and decompress bundle
        raw_bytes = file.read()
        if len(raw_bytes) > MAX_BUNDLE_SIZE:
            return jsonify({"error": f"File too large. Max {MAX_BUNDLE_SIZE // (1024*1024)}MB"}), 400
        
        decompressed = decompress_stream(raw_bytes)
        
        # Load with UnityPy
        try:
            env = UnityPy.load(decompressed)
        except Exception as e:
            return jsonify({"error": f"Invalid Unity bundle format: {str(e)}"}), 400
        
        # Extract all assets
        extracted_assets = []
        manifest = []
        seen_hashes = set()
        asset_counter = 0
        
        for obj in env.objects:
            result = process_object_unrestricted(obj, decompressed, asset_counter)
            if result:
                filename, asset_bytes, zip_path = result
                
                # Deduplicate by content hash
                content_hash = hashlib.md5(asset_bytes).hexdigest()
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    
                    extracted_assets.append({
                        'name': filename,
                        'zip_path': zip_path,
                        'bytes': asset_bytes
                    })
                    
                    manifest.append({
                        'index': asset_counter,
                        'name': filename,
                        'path': zip_path,
                        'size': len(asset_bytes),
                        'hash': content_hash[:8]
                    })
                    asset_counter += 1
        
        # Clean up UnityPy environment
        del env
        gc.collect()
        
        if asset_counter == 0:
            return jsonify({"error": "No supported assets found in the bundle. The bundle may be empty or use unsupported compression."}), 400
        
        # Store in cache
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_assets
        GLOBAL_CACHE_REGISTRY['timestamp'] = hash(str(raw_bytes[:1000]))
        
        return jsonify({
            "files": manifest,
            "total_assets": asset_counter,
            "bundle_name": file.filename,
            "status": "success"
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Extraction failed: {str(e)}"}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "healthy", "cache_size": len(GLOBAL_CACHE_REGISTRY.get('extracted', []))})


if __name__ == '__main__':
    print("=" * 60)
    print("🎮 UNIVERSAL ASSET EXTRACTOR - Unity Bundle Parser")
    print(f"📁 Supported types: {', '.join(SUPPORTED_TYPES[:8])} + more")
    print("🌐 Starting server at http://localhost:5000")
    print("=" * 60)
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)