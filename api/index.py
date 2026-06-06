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
from PIL import Image
import texture2ddecoder
import UnityPy

os.environ["UNITYPY_NO_GUI"] = "1"

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')
GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    try:
        if data.startswith(b'\x1f\x8b'):
            return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
            return decompress_stream(zlib.decompress(data))
    except:
        pass
    return data

def mesh_to_obj(mesh) -> str:
    try:
        lines = [f"g {mesh.name}"]
        for v in mesh.m_Vertices:
            lines.append(f"v {-v.x} {v.y} {v.z}")
        for vn in mesh.m_Normals:
            lines.append(f"vn {-vn.x} {vn.y} {vn.z}")
        for vt in mesh.m_UV0:
            lines.append(f"vt {vt.x} {vt.y}")
        
        for sub in mesh.m_SubMeshes:
            index_data = mesh.m_IndexBuffer[sub.firstByte : sub.firstByte + sub.indexCount * 2]
            indices = [struct.unpack('<H', index_data[i:i+2])[0] for i in range(0, len(index_data), 2)]
            for i in range(0, len(indices), 3):
                # OBJ indices are 1-based
                f1, f2, f3 = indices[i]+1, indices[i+1]+1, indices[i+2]+1
                lines.append(f"f {f1}/{f1}/{f1} {f2}/{f2}/{f2} {f3}/{f3}/{f3}")
        return "\n".join(lines)
    except:
        return ""

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

def dump_obj_to_dict(obj_data) -> dict:
    out = {}
    try:
        if hasattr(obj_data, "read_typetree"):
            return obj_data.read_typetree()
    except:
        pass
    for attr in dir(obj_data):
        if attr.startswith('_') or attr in ['read', 'assets_file', 'reader', 'image', 'samples']:
            continue
        try:
            val = getattr(obj_data, attr)
            if isinstance(val, (int, float, str, bool)):
                out[attr] = val
            elif isinstance(val, bytes):
                out[attr] = val.hex()[:500] + "..." if len(val) > 500 else val.hex()
        except:
            pass
    return out

def process_object_unrestricted(obj, raw_env_data: bytes):
    try:
        t = obj.type.name
        data = obj.read()
        p_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", p_name)

        # 1. Textures & Sprites
        if t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG")
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", f"{t}"

        # 2. Audio
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio"

        # 3. Meshes (3D Objects)
        elif t == "Mesh":
            obj_content = mesh_to_obj(data)
            if obj_content:
                return f"{safe_name}.obj", obj_content.encode('utf-8'), f"Meshes/{safe_name}.obj", "3D Mesh"
            tree_data = dump_obj_to_dict(data)
            return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Meshes/{safe_name}.json", "Mesh Data"

        # 4. Text & Config
        elif t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8')
            ext = ".txt"
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or b"size:" in raw:
                ext = ".atlas"
                label = "Atlas"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        # 5. Video
        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    raw = raw_env_data[match-4:match+12000000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video"

        # 6. Logic & Scripts
        elif t in ["MonoBehaviour", "GameObject", "ScriptableObject"]:
            tree_data = dump_obj_to_dict(data)
            js = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}.json", js, f"Scripts/{t}/{safe_name}.json", t

        # 7. Rendering (Shaders/Materials)
        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            js = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}.json", js, f"Shaders_Materials/{safe_name}.json", t

        # 8. Fonts
        elif t == "Font":
            raw_font = getattr(data, "m_FontData", b"")
            if len(raw_font) > 10:
                ext = ".otf" if raw_font.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font, f"Fonts/{safe_name}{ext}", "Font"

        # 9. Asset Bundles
        elif t == "AssetBundle":
            tree_data = dump_obj_to_dict(data)
            js = json.dumps(tree_data, indent=2).encode('utf-8')
            return f"{safe_name}_manifest.json", js, f"Bundles/{safe_name}_manifest.json", "Bundle"

        # Fallback
        tree_data = dump_obj_to_dict(data)
        if tree_data:
            return f"{safe_name}.json", json.dumps(tree_data, indent=2).encode('utf-8'), f"Other/{t}/{safe_name}.json", t
        
        raw_bytes = obj.get_raw_data()
        if raw_bytes:
            return f"{safe_name}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", f"Binary {t}"
    except:
        pass
    return None

def handle_astc_ktx_decoding(data, name):
    try:
        # Check for KTX or ASTC magic
        if data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
            # KTX logic (Simplified for space, using texture2ddecoder)
            header = data[:64]
            gl_fmt = struct.unpack('<I', header[28:32])[0]
            w = struct.unpack('<I', header[36:40])[0]
            h = struct.unpack('<I', header[40:44])[0]
            offset = 64 + struct.unpack('<I', header[60:64])[0]
            img_data = data[offset+4:]
            
            if gl_fmt == 0x8D64: # ETC1
                decoded = texture2ddecoder.decode_etc1(img_data, w, h)
            elif 0x93B0 <= gl_fmt <= 0x93BD: # ASTC
                # Using a generic 4x4 block for KTX-wrapped ASTC
                decoded = texture2ddecoder.decode_astc(img_data, w, h, 4, 4)
            else:
                return None
            
            img = Image.frombytes("RGBA", (w, h), decoded).transpose(Image.FLIP_TOP_BOTTOM)
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
            
        elif data.startswith(b'\x13\xAB\xA1\x5C'): # Raw ASTC
            block_w = data[4]
            block_h = data[5]
            w = data[7] | (data[8] << 8) | (data[9] << 16)
            h = data[10] | (data[11] << 8) | (data[12] << 16)
            decoded = texture2ddecoder.decode_astc(data[16:], w, h, block_w, block_h)
            img = Image.frombytes("RGBA", (w, h), decoded).transpose(Image.FLIP_TOP_BOTTOM)
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except:
        pass
    return None

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    download_type = request.args.get('download_type', '')
    
    if download_type == 'zip':
        mode = request.args.get('mode', 'normal')
        indices = request.args.get('indices', '') # For filtered downloads
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "No cache found"}), 400
        
        target_indices = []
        if indices:
            target_indices = [int(i) for i in indices.split(',') if i.isdigit()]
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(GLOBAL_CACHE_REGISTRY['extracted']):
                if target_indices and idx not in target_indices:
                    continue
                
                path = item['zip_path'] if mode == 'grouped' else item['name']
                zf.writestr(path, item['bytes'])
        
        zip_io.seek(0)
        zip_name = GLOBAL_CACHE_REGISTRY.get('orig_name', 'assets')
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=f"{zip_name}[Extracted].zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Invalid index"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "No file"}), 400

    try:
        up_file = request.files['asset_bundle']
        orig_name = os.path.splitext(up_file.filename)[0].split('-')[0].split('.')[0]
        raw_bytes = up_file.read()
        decompressed = decompress_stream(raw_bytes)
        
        extracted_list = []
        json_manifest = []
        seen_md5 = set()
        
        # Try ASTC/KTX Direct
        decoded_img = handle_astc_ktx_decoding(decompressed, orig_name)
        if decoded_img:
            extracted_list.append({'name': f"{orig_name}.png", 'zip_path': f"Textures/{orig_name}.png", 'bytes': decoded_img})
            json_manifest.append({'index': 0, 'name': f"{orig_name}.png", 'label': "Image (ASTC/KTX)"})
        else:
            # Try UnityPy
            try:
                env = UnityPy.load(decompressed)
                for obj in env.objects:
                    res = process_object_unrestricted(obj, decompressed)
                    if res:
                        fname, fbytes, zpath, label = res
                        h = hashlib.md5(fbytes).hexdigest()
                        if h not in seen_md5:
                            seen_md5.add(h)
                            idx = len(extracted_list)
                            extracted_list.append({'name': fname, 'zip_path': zpath, 'bytes': fbytes})
                            json_manifest.append({'index': idx, 'name': fname, 'label': label})
                del env
            except:
                return jsonify({"error": "Failed to parse file"}), 400

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        GLOBAL_CACHE_REGISTRY['orig_name'] = orig_name
        gc.collect()
        return jsonify({"files": json_manifest})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000)