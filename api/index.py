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

GLOBAL_CACHE_REGISTRY = {}

def decompress_stream(data: bytes) -> bytes:
    """Strips compression wrappers from incoming data array payloads."""
    try:
        if data.startswith(b'\x1f\x8b'): return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')): return decompress_stream(zlib.decompress(data))
    except: pass
    return data

def extract_clean_name(obj, data, default_type: str) -> str:
    """Extracts internal active identifier tags bound to individual classes."""
    if hasattr(obj, 'container') and obj.container:
        base_mapped_path = os.path.basename(obj.container)
        if base_mapped_path:
            return os.path.splitext(base_mapped_path)[0]
            
    for attr in ["name", "m_Name", "m_name"]:
        val = getattr(data, attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
            
    return f"{default_type}_{obj.path_id}"

def process_object_unrestricted(obj, raw_env_data):
    try:
        t = obj.type.name
        data = obj.read()

        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", pristine_name)

        # ------------------------
        # TEXT ASSETS
        # ------------------------
        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str):
                raw = raw.encode("utf-8", errors="replace")

            ext = ".json" if raw.startswith((b"{", b"[")) else ".txt"
            return f"{safe_name}{ext}", raw, f"TextAssets/{safe_name}{ext}"

        # ------------------------
        # IMAGES
        # ------------------------
        elif t in ["Texture2D", "Sprite"]:

    try:
        img = data.image

        buf = io.BytesIO()
        img.save(buf, format="PNG")

        return (
            f"{safe_name}.png",
            buf.getvalue(),
            f"Images/{safe_name}.png"
        )

    except Exception as e:
        print("IMAGE ERROR", e)
            if hasattr(data, "image"):
                buf = io.BytesIO()
                data.image.save(buf, format="PNG")
                return (
                    f"{safe_name}.png",
                    buf.getvalue(),
                    f"Images/{safe_name}.png"
                )

        # ------------------------
        # AUDIO
        # ------------------------
        elif t == "AudioClip":
            samples = getattr(data, "samples", None)

            if samples:
                first = list(samples.keys())[0]
                return (
                    first,
                    samples[first],
                    f"Audio/{first}"
                )

            raw = obj.get_raw_data()
            return (
                f"{safe_name}.bin",
                raw,
                f"Audio/{safe_name}.bin"
            )

        # ------------------------
        # VIDEO
        # ------------------------
        elif t == "VideoClip":
            raw = obj.get_raw_data()

            return (
                f"{safe_name}.mp4",
                raw,
                f"Video/{safe_name}.mp4"
            )

        # ------------------------
        # SHADERS
        # ------------------------
        elif t == "Shader":
            try:
                txt = str(data.export())
            except:
                txt = str(data.read_typetree())

            return (
                f"{safe_name}.shader",
                txt.encode("utf-8"),
                f"Shaders/{safe_name}.shader"
            )

        # ------------------------
        # MONOBEHAVIOUR
        # ------------------------
        elif t == "MonoBehaviour":

    try:
        tree = data.read_typetree()

        return (
            f"{safe_name}.json",
            json.dumps(
                tree,
                indent=2,
                ensure_ascii=False
            ).encode("utf-8"),
            f"MonoBehaviour/{safe_name}.json"
        )

    except Exception:

        try:
            raw = obj.get_raw_data()

            return (
                f"{safe_name}.bytes",
                raw,
                f"MonoBehaviour/{safe_name}.bytes"
            )
        except:
            pass

        # ------------------------
        # MATERIAL
        # ------------------------
        elif t == "Material":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"Materials/{safe_name}.json"
            )

        # ------------------------
        # GAMEOBJECT
        # ------------------------
        elif t == "GameObject":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"GameObjects/{safe_name}.json"
            )

        # ------------------------
        # ANIMATION
        # ------------------------
        elif t in [
            "AnimationClip",
            "AnimatorController",
            "AnimatorOverrideController"
        ]:
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"Animations/{safe_name}.json"
            )

        # ------------------------
        # SPRITE ATLAS
        # ------------------------
        elif t == "SpriteAtlas":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"SpriteAtlas/{safe_name}.json"
            )

        # ------------------------
        # FONTS
        # ------------------------
        elif t in [
            "Font",
            "TMP_FontAsset"
        ]:
            raw = obj.get_raw_data()

            return (
                f"{safe_name}.font",
                raw,
                f"Fonts/{safe_name}.font"
            )

        # ------------------------
        # MESH
        # ------------------------
        elif t == "Mesh":
            raw = obj.get_raw_data()

            return (
                f"{safe_name}.mesh",
                raw,
                f"Meshes/{safe_name}.mesh"
            )

        # ------------------------
        # ASSETBUNDLE
        # ------------------------
        elif t == "AssetBundle":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"AssetBundle/{safe_name}.json"
            )

        # ------------------------
        # AVATAR
        # ------------------------
        elif t == "Avatar":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"Avatar/{safe_name}.json"
            )

        # ------------------------
        # TERRAIN
        # ------------------------
        elif t == "TerrainData":
            tree = data.read_typetree()

            return (
                f"{safe_name}.json",
                json.dumps(tree, indent=2).encode(),
                f"Terrain/{safe_name}.json"
            )

        # ------------------------
        # RAW FALLBACK
        # ------------------------
        else:
            raw = obj.get_raw_data()

            if raw:
                return (
                    f"{safe_name}.bin",
                    raw,
                    f"Raw/{safe_name}.bin"
                )

    except Exception as e:
    try:
        raw = obj.get_raw_data()

        name = f"{obj.type.name}_{obj.path_id}"

        return (
            f"{name}.bin",
            raw,
            f"Failed/{name}.bin"
        )
    except:
        print(f"ERROR {obj.type.name}: {e}")

return None

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    if path in ["api/extract", "api/extract/"] and request.method == "POST":
        return "POST stream pathways execute on specific backend routes exclusively.", 405
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Interface layout missing or broken: {str(e)}", 500

@app.route('/api/extract', methods=['POST'])
def handle_direct_extraction_stream():
    global GLOBAL_CACHE_REGISTRY
    
    download_type = request.args.get('download_type', '')

    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'):
            return jsonify({"error": "Cache registry empty. Re-stream source package container."}), 400
        
        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for item in GLOBAL_CACHE_REGISTRY['extracted']:
                zf.writestr(item['zip_path'], item['bytes'])
        zip_io.seek(0)
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name="extracted_assets.zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Target mapping index reference lost."}), 400
        
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files:
        return jsonify({"error": "Multipart byte payload context missing."}), 400

    try:
        raw_bundle_bytes = request.files['asset_bundle'].read()
        final_data = decompress_stream(raw_bundle_bytes)
        
        try:
            env = UnityPy.load(final_data)
            objects_array = env.objects
        except Exception:
            return jsonify({"error": "Invalid format layout. Standard package headers not verified."}), 400
        
        seen_md5 = set()
        extracted_list = []
        json_metadata_manifest = []
        tracking_index_counter = 0

        for obj in objects_array:
        print("TYPE:", obj.type.name)
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

        del env
        gc.collect()

        if tracking_index_counter == 0:
            return jsonify({"error": "No valid supported structural elements recognized inside files."}), 400

        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        return jsonify({"files": json_metadata_manifest})

    except Exception as e:
        return jsonify({"error": f"Internal execution thread pipeline exception: {str(e)}"}), 500