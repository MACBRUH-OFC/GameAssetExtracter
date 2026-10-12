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
import urllib.request
import base64
from urllib.parse import unquote
from flask import Flask, request, send_file, jsonify
from PIL import Image
import texture2ddecoder
os.environ["UNITYPY_NO_GUI"] = "1"
import UnityPy

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, 'index.html')
GLOBAL_CACHE_REGISTRY = {}

def configure_assetbundle_decrypt_key():
    """Configure UnityCN decryption from a Vercel environment secret, if set."""
    configured_key = os.environ.get("UNITY_ASSETBUNDLE_DECRYPT_KEY", "").strip()
    if not configured_key:
        return False

    try:
        key = bytes.fromhex(configured_key) if re.fullmatch(r"[0-9a-fA-F]{32}", configured_key) else configured_key.encode("utf-8")
        UnityPy.set_assetbundle_decrypt_key(key)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("UNITY_ASSETBUNDLE_DECRYPT_KEY must be a 16-byte key or 32 hexadecimal characters") from exc
    return True

def unityfs_encryption_info(data: bytes):
    """Read UnityFS header metadata and identify legacy UnityCN encryption."""
    if not data.startswith(b"UnityFS\0"):
        return None
    try:
        pos = 12
        versions = []
        for _ in range(2):
            end = data.index(b"\0", pos)
            versions.append(data[pos:end].decode("ascii", "replace"))
            pos = end + 1
        pos += 16
        flags = struct.unpack_from(">I", data, pos)[0]
        match = re.match(r"(\d+)\.", versions[-1])
        major = int(match.group(1)) if match else 0
        # The 2018 bundles in this update use UnityCN's legacy 0x200 flag.
        # On newer Unity versions that bit can mean alignment instead.
        return {"version": versions[-1], "flags": flags, "unitycn_encrypted": bool(flags & 0x200) and major < 2020}
    except (ValueError, IndexError, struct.error):
        return None

def infer_bundle_content(filename: str):
    """Give callers a cautious content hint from a package name."""
    name = os.path.basename(filename).lower()
    hints = [
        ("audio", ("bgm", "audio", "sound", "voice")),
        ("texture", ("uifx", "texture", "sprite", "loading_quality", "background")),
        ("text/configuration", ("localization", "lochotfix", "quickchat", "config", "patch", "hotfix")),
        ("visual/model", ("model", "mesh", "skin", "reskin", "obj")),
    ]
    return next((label for label, terms in hints if any(term in name for term in terms)), "game data")

def protected_bundle_error(filename: str, data: bytes, texture_metadata):
    info = unityfs_encryption_info(data) or {}
    kind = infer_bundle_content(filename)
    detail = ""
    if texture_metadata:
        found = ", ".join(
            f"{item['name']} ({item['width']}×{item['height']} ETC_RGB4)"
            for item in texture_metadata[:8]
        )
        detail = f" Found {len(texture_metadata)} texture record(s): {found}. Pixel data is referenced from streamed .resS resources, which this bundle parse did not recover."
    return {
        "error": f"UnityCN-encrypted UnityFS bundle detected. The {kind} payload was not decoded; this is not a confirmed empty-asset result." + detail,
        "code": "unitycn_payload_unavailable",
        "bundle_format": "UnityFS",
        "bundle_version": info.get("version"),
        "encryption_flags": info.get("flags"),
        "content_hint": kind,
        "extraction_complete": False,
        "detected_textures": texture_metadata,
    }

def load_unity_bundle(data: bytes):
    """Best-effort salvage of readable entries from UnityCN bundles.

    Clearing the old-engine 0x200 encryption flag in a private copy lets
    UnityPy inspect unencrypted bundle metadata and any readable serialized
    entries. It does not decrypt protected CAB blocks, so callers must treat
    zero or partial objects as an incomplete extraction.
    """
    try:
        return UnityPy.load(data)
    except LookupError as exc:
        if "BundleFile is encrypted" not in str(exc) or not data.startswith(b"UnityFS\0"):
            raise

    # UnityFS header: signature, format version, two null-terminated version
    # strings, file size, and two block sizes, followed by flags.
    pos = 8
    try:
        _, = struct.unpack_from(">I", data, pos)
        pos += 4
        for _ in range(2):
            end = data.index(b"\0", pos)
            pos = end + 1
        pos += 8 + 4 + 4
        flags = struct.unpack_from(">I", data, pos)[0]
        if not flags & 0x200:
            raise ValueError("UnityFS encryption flag not present")
        patched = bytearray(data)
        struct.pack_into(">I", patched, pos, flags & ~0x200)
        return UnityPy.load(bytes(patched))
    except (ValueError, IndexError, struct.error) as exc:
        raise LookupError("Could not safely retry UnityFS flag compatibility") from exc

def decompress_stream(data: bytes) -> bytes:
    try:
        if data.startswith(b'\x1f\x8b'):
            return decompress_stream(gzip.decompress(data))
        if data.startswith((b'\x78\x9c', b'\x78\x01', b'\x78\xda')):
            return decompress_stream(zlib.decompress(data))
    except:
        pass
    return data

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

def scan_streamed_etc1_textures(data: bytes):
    """Recognize Texture2D metadata that the mobile raw scanner can see.

    Some mini-update bundles leave the serialized Texture2D record readable
    while keeping its pixel bytes in an external archive .resS stream. Report
    those records for diagnostics; never treat guessed bytes as an image.
    """
    max_dimension = 8192
    records = []
    seen = set()

    for object_start in range(0, max(0, len(data) - 0x8C), 4):
        width, height, image_size = struct.unpack_from("<III", data, object_start + 0x20)
        texture_format = struct.unpack_from("<I", data, object_start + 0x30)[0]
        if texture_format != 34 or not (1 <= width <= max_dimension and 1 <= height <= max_dimension):
            continue
        expected_size = ((width + 3) // 4) * ((height + 3) // 4) * 8
        if image_size != expected_size or image_size > len(data):
            continue

        stream_offset = struct.unpack_from("<Q", data, object_start + 0x78)[0]
        stream_size = struct.unpack_from("<I", data, object_start + 0x80)[0]
        path_size = struct.unpack_from("<I", data, object_start + 0x84)[0]
        if stream_size != image_size or not (1 <= path_size <= 512):
            continue
        path_start = object_start + 0x88
        stream_path = data[path_start:path_start + path_size].split(b"\0", 1)[0].decode("utf-8", "replace")
        if not stream_path.lower().endswith(".ress"):
            continue

        name_size = struct.unpack_from("<I", data, object_start)[0]
        name = ""
        if 1 <= name_size <= 150:
            candidate = data[object_start + 4:object_start + 4 + name_size].decode("utf-8", "replace").strip("\0")
            if candidate and all(ch.isprintable() for ch in candidate):
                name = candidate
        if not name:
            scan_start = max(0, object_start - 0x180)
            scan_end = min(len(data), object_start + 0x300)
            candidates = re.findall(rb"[A-Za-z][A-Za-z0-9_./-]{2,150}", data[scan_start:scan_end])
            ignored = {b"Texture2D", b"Texture", b"Material", b"Shader", b"Sprite", b"GameObject", b"Transform", b"MonoBehaviour", b"UnityEngine", b"Unity", b"Assets", b"Resources"}
            names = [item.decode("ascii", "ignore") for item in candidates if item not in ignored and not item.lower().startswith((b"http://", b"https://", b"www."))]
            if names:
                name = max(names, key=lambda item: ("_" in item, any(ch.isupper() for ch in item), len(item)))
        if not name or not all(ch.isprintable() for ch in name):
            continue

        identity = (object_start, name, width, height)
        if identity in seen:
            continue
        seen.add(identity)
        records.append({
            "name": name,
            "width": width,
            "height": height,
            "format": "ETC_RGB4",
            "stream_path": stream_path,
            "stream_offset": stream_offset,
            "stream_size": stream_size,
            "metadata_offset": object_start,
        })
    return records


def texture_record_key(record):
    """Use the serialized record location so duplicate names remain distinct."""
    return (
        record.get("metadata_offset"),
        record.get("name"),
        record.get("width"),
        record.get("height"),
        record.get("stream_offset"),
        record.get("stream_size"),
    )


class HttpRangeReader:
    """Small seekable HTTP reader for opening selected files in a remote ZIP."""
    def __init__(self, url: str, block_size: int = 1024 * 1024):
        self.url = url
        self.block_size = block_size
        self.position = 0
        self.blocks = {}
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            self.size = int(response.headers["Content-Length"])

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        self.position = max(0, min(self.size, target))
        return self.position

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        output = bytearray()
        while size:
            block_index = self.position // self.block_size
            block_start = block_index * self.block_size
            if block_index not in self.blocks:
                block_end = min(self.size, block_start + self.block_size) - 1
                request = urllib.request.Request(
                    self.url,
                    headers={"Range": f"bytes={block_start}-{block_end}", "User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status != 206:
                        raise IOError("APK server does not support ranged reads")
                    self.blocks[block_index] = response.read()
            block = self.blocks[block_index]
            offset = self.position - block_start
            count = min(size, len(block) - offset)
            if count <= 0:
                break
            output.extend(block[offset:offset + count])
            self.position += count
            size -= count
        return bytes(output)


# Stable entries from the official Free Fire APK. Every use is checked against
# the Unity Texture2D name, dimensions, and ETC1 byte count before extraction.
BASE_APK_TEXTURE_ENTRIES = {
    "UIFX_Glow13": "assets/bin/Data/866fa08ca97b0cc49817779188e073e5",
    "UIFX_Glow16": "assets/bin/Data/faa49e011d8989142886a7efa9803b94",
    "UIFX_Mask2": "assets/bin/Data/0f01f22a5e107d94a8f6616bfcc14c61",
    "UIFX_Mask206_Clamp": "assets/bin/Data/af5500e4da10cea4ab979ecdaf745d26",
    "UIFX_Mask223_Clamp": "assets/bin/Data/5bfecb6b26d1ea8439024436b26cebde",
    "UIFX_Mask41_clamp": "assets/bin/Data/5b32b11d915d3d54fadc8aa33f3319e8",
    "UIFX_Mask53_Clamp": "assets/bin/Data/16ebc41ff7472174db69197c095f7369",
    "UIFX_Mask64_Clamp": "assets/bin/Data/333f6dc19d777cd4ca2738b626cc5404",
    "UIFX_Mask75": "assets/bin/Data/1299a455c74057e47a493611584ba8d5",
    "UIFX_Noise21": "assets/bin/Data/e970c8b7b4cfa6d4e9a30b3356e4a0d8",
    "UIFX_Noise3": "assets/bin/Data/0e49ae35286c98a44a73de9a4dc519d8",
    "UIFX_Noise33": "assets/bin/Data/f22548d62bbc90f46acdd9c39d708a93",
    "UIFX_Noise54": "assets/bin/Data/c4d13bbbebd7679459e622618d37b2fe",
    "UIFX_Noise6": "assets/bin/Data/268dc7b9d7d6f5c478be95956251ddec",
}


def recover_texture_assets_from_bundle_streams(env, texture_metadata):
    """Decode Texture2D ETC1 payloads from the bundle's internal .resS files."""
    stream_readers = {}
    for bundle in getattr(env, "files", {}).values():
        for name, reader in getattr(bundle, "files", {}).items():
            if isinstance(name, str) and name.lower().endswith(".ress"):
                stream_readers[os.path.basename(name).lower()] = reader

    extracted = []
    manifest = []
    recovered = set()
    used_names = set()
    for record in texture_metadata:
        if record.get("format") != "ETC_RGB4":
            continue
        width, height = record.get("width"), record.get("height")
        size, offset = record.get("stream_size"), record.get("stream_offset")
        if not all(isinstance(value, int) for value in (width, height, size, offset)):
            continue
        expected_size = ((width + 3) // 4) * ((height + 3) // 4) * 8
        if not (1 <= width <= 8192 and 1 <= height <= 8192 and size == expected_size and offset >= 0):
            continue
        stream_name = os.path.basename(record.get("stream_path", "")).lower()
        reader = stream_readers.get(stream_name)
        if reader is None or offset + size > reader.Length:
            continue

        original_position = reader.tell()
        try:
            reader.seek(offset)
            payload = reader.read(size)
        finally:
            reader.seek(original_position)
        if len(payload) != size:
            continue

        try:
            pixels = texture2ddecoder.decode_etc1(payload, width, height)
            if len(pixels) != width * height * 4:
                continue
            image = Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA")
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            png_io = io.BytesIO()
            image.save(png_io, format="PNG")
        except Exception as exc:
            print(f"Could not decode streamed ETC1 texture {record.get('name')}: {exc}")
            continue

        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", record.get("name") or "Texture")
        base_name = safe_name
        suffix = 2
        while safe_name.lower() in used_names:
            safe_name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(safe_name.lower())
        index = len(extracted)
        for filename, content, label in (
            (f"{safe_name}.png", png_io.getvalue(), "Texture Image"),
            (f"{safe_name}.etc1", payload, "Original ETC1 Texture Data"),
        ):
            extracted.append({"index": index, "name": filename, "zip_path": f"Textures/{filename}", "bytes": content, "label": label})
            manifest.append({
                "index": index,
                "name": filename,
                "path": f"Textures/{filename}",
                "label": label,
                "size": len(content),
                "data": base64.b64encode(content).decode("ascii"),
            })
            index += 1
        recovered.add(texture_record_key(record))

    return extracted, recovered


def recover_texture_assets_from_base_apk(texture_metadata, already_recovered=()):
    """Recover exact matching ETC1 streams from the official APK base assets."""
    recovered = set(already_recovered)
    wanted = {}
    for record in texture_metadata:
        record_key = texture_record_key(record)
        if record_key in recovered:
            continue
        entry = BASE_APK_TEXTURE_ENTRIES.get(record["name"])
        if entry:
            wanted.setdefault(entry, []).append(record)
    if not wanted:
        unresolved = [r for r in texture_metadata if texture_record_key(r) not in recovered]
        return [], [], unresolved

    extracted = []
    manifest = []
    try:
        apk_reader = HttpRangeReader("https://dl.cdn.freefiremobile.com/live/package/FreeFire.apk")
        with zipfile.ZipFile(apk_reader) as apk:
            for entry, records in wanted.items():
                try:
                    raw_asset = apk.read(entry)
                    asset_env = UnityPy.load(raw_asset)
                except Exception as exc:
                    print(f"Could not read APK texture entry {entry}: {exc}")
                    continue

                for obj in asset_env.objects:
                    if obj.type.name != "Texture2D":
                        continue
                    try:
                        tree = obj.read_typetree(check_read=False)
                        name = tree.get("m_Name")
                        width = tree.get("m_Width")
                        height = tree.get("m_Height")
                        image_size = tree.get("m_CompleteImageSize")
                        if not (isinstance(name, str) and isinstance(width, int) and
                                isinstance(height, int) and isinstance(image_size, int) and
                                1 <= width <= 8192 and 1 <= height <= 8192):
                            continue
                        expected_size = ((width + 3) // 4) * ((height + 3) // 4) * 8
                        if image_size != expected_size:
                            continue
                        record = next((r for r in records if
                            r["name"] == name and r["width"] == width and
                            r["height"] == height and r["stream_size"] == image_size), None)
                        if not record:
                            continue

                        object_bytes = obj.get_raw_data()
                        if len(object_bytes) - image_size not in (128, 132, 136):
                            continue
                        payload = object_bytes[-image_size:]
                        pixels = texture2ddecoder.decode_etc1(payload, width, height)
                        image_io = io.BytesIO()
                        image = Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA")
                        image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(image_io, format="PNG")
                        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
                        index = len(extracted)
                        files = [
                            (f"{safe_name}.png", image_io.getvalue(), "Texture Image"),
                            (f"{safe_name}.etc1", payload, "Original ETC1 Texture Data"),
                        ]
                        for filename, content, label in files:
                            zip_path = f"Textures/{filename}"
                            extracted.append({"index": index, "name": filename, "zip_path": zip_path, "bytes": content, "label": label})
                            manifest.append({
                                "index": index,
                                "name": filename,
                                "path": zip_path,
                                "label": label,
                                "size": len(content),
                                "data": base64.b64encode(content).decode("ascii"),
                            })
                            index += 1
                        recovered.add(texture_record_key(record))
                    except Exception as exc:
                        print(f"Could not decode APK Texture2D in {entry}: {exc}")
    except Exception as exc:
        print(f"Could not open official APK by ranged read: {exc}")

    unresolved = [r for r in texture_metadata if texture_record_key(r) not in recovered]
    return extracted, manifest, unresolved

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
    t = "Unknown"
    try:
        t = obj.type.name
        data = obj.read()
        pristine_name = extract_clean_name(obj, data, t)
        safe_name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "", pristine_name)

        if t == "TextAsset":
            raw = getattr(data, "m_Script", b"")
            if isinstance(raw, str): raw = raw.encode('utf-8', errors='replace')
            ext = ".txt"
            label = "Text File"
            if safe_name.lower().endswith('.atlas') or raw.startswith(b"\n") or b"size:" in raw:
                if not safe_name.lower().endswith('.atlas'): ext = ".atlas.txt"
                label = "Atlas Sheet"
            elif raw.startswith((b"{", b"[")):
                ext = ".json"
                label = "Data Config"
            return f"{safe_name}{ext}", raw, f"Text/{safe_name}{ext}", label

        elif t in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            buf = io.BytesIO()
            data.image.save(buf, format="PNG", optimize=False)
            return f"{safe_name}.png", buf.getvalue(), f"Textures/{safe_name}.png", f"{t} Asset"

        elif t == "SpriteAtlas":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_atlas.json", js_bytes, f"Mapping/{safe_name}_atlas.json", "SpriteAtlas Map"

        elif t == "AudioClip":
            samples = getattr(data, "samples", None)
            if samples and list(samples.keys()):
                audio_filename = list(samples.keys())[0]
                return audio_filename, samples[audio_filename], f"Audio/{audio_filename}", "Audio Track"
            raw = obj.get_raw_data()
            ext = ".ogg" if raw.startswith(b'OggS') else ".wav"
            return f"{safe_name}{ext}", raw, f"Audio/{safe_name}{ext}", "Audio Track"

        elif t == "VideoClip":
            raw = obj.get_raw_data()
            if len(raw) < 1024:
                match = raw_env_data.find(b'ftyp')
                if match != -1:
                    start_pos = max(0, match - 4)
                    raw = raw_env_data[start_pos:start_pos + 15_000_000]
            return f"{safe_name}.mp4", raw, f"Video/{safe_name}.mp4", "Video Clip"

        elif t == "Mesh":
            try:
                mesh_data = data.export().encode('utf-8')
                return f"{safe_name}.obj", mesh_data, f"Meshes/{safe_name}.obj", "3D Mesh"
            except:
                tree_data = dump_obj_to_dict(data)
                js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                return f"{safe_name}_mesh.json", js_bytes, f"Geometry/Mesh/{safe_name}.json", "Mesh Schema"

        elif t in ["GameObject", "MonoBehaviour", "ScriptableObject", "SkinnedMeshRenderer", "MeshRenderer"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            folder = "Hierarchy" if t == "GameObject" else "Scripts" if "Script" in t else "Geometry"
            return f"{safe_name}_{t}.json", js_bytes, f"{folder}/{t}/{safe_name}.json", f"{t} Schema"

        elif t in ["Material", "Shader"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Shaders_Materials/{t}/{safe_name}.json", f"{t} Config"

        elif t in ["AnimationClip", "AnimatorController", "Animator"]:
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_{t}.json", js_bytes, f"Animations/{t}/{safe_name}.json", "Animation Map"

        elif t == "Font":
            raw_font_data = getattr(data, "m_FontData", b"")
            if raw_font_data and len(raw_font_data) > 10:
                ext = ".otf" if raw_font_data.startswith(b'OTTO') else ".ttf"
                return f"{safe_name}{ext}", raw_font_data, f"Fonts/{safe_name}{ext}", "Font File"
            return f"{safe_name}_font.json", json.dumps(dump_obj_to_dict(data)).encode('utf-8'), f"Fonts/{safe_name}.json", "Font Metadata"

        elif t == "AssetBundle":
            tree_data = dump_obj_to_dict(data)
            js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
            return f"{safe_name}_manifest.json", js_bytes, f"Containers/{safe_name}.json", "Bundle Manifest"

        else:
            try:
                tree_data = dump_obj_to_dict(data)
                if tree_data:
                    js_bytes = json.dumps(tree_data, indent=2, ensure_ascii=False).encode('utf-8')
                    return f"{safe_name}_{t}.json", js_bytes, f"Other/{t}/{safe_name}.json", f"Data ({t})"
            except: pass
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                return f"{safe_name}_{t}.dat", raw_bytes, f"Other/{t}/{safe_name}.dat", f"Binary ({t})"
    except Exception as exc:
        # A malformed or not-yet-supported Unity object should not make the
        # whole bundle look empty. Preserve its raw serialized bytes instead.
        try:
            raw_bytes = obj.get_raw_data()
            if raw_bytes:
                path_id = getattr(obj, "path_id", "unknown")
                safe_type = re.sub(r"[^A-Za-z0-9_.-]", "_", str(t)) or "Unknown"
                name = f"{safe_type}_{path_id}.dat"
                return name, raw_bytes, f"Other/{safe_type}/{name}", "Raw Unity Object"
        except Exception:
            pass
        print(f"Skipping Unity object {t}: {exc}")
    return None

def convert_ktx_to_png_fallback(file_bytes) -> bytes:
    f = io.BytesIO(file_bytes)
    header = f.read(64)
    if len(header) < 64 or header[:12] != b'\xABKTX 11\xBB\r\n\x1A\n': raise Exception("Invalid KTX")
    gl_internal_format = struct.unpack('<I', header[28:32])[0]
    width = struct.unpack('<I', header[36:40])[0]
    height = struct.unpack('<I', header[40:44])[0]
    bytes_of_kv = struct.unpack('<I', header[60:64])[0]
    f.seek(64 + bytes_of_kv)
    image_size = struct.unpack('<I', f.read(4))[0]
    data = f.read(image_size)
    if gl_internal_format == 0x8D64: decoded = texture2ddecoder.decode_etc1(data, width, height)
    elif 0x93B0 <= gl_internal_format <= 0x93BD:
        astc_formats = {0x93B0:(4,4), 0x93B1:(5,4), 0x93B2:(5,5), 0x93B3:(6,5), 0x93B4:(6,6), 0x93B5:(8,5), 0x93B6:(8,6), 0x93B7:(8,8), 0x93B8:(10,5), 0x93B9:(10,6), 0x93BA:(10,8), 0x93BB:(10,10), 0x93BC:(12,10), 0x93BD:(12,12)}
        bx, by = astc_formats[gl_internal_format]
        decoded = texture2ddecoder.decode_astc(data, width, height, bx, by)
    elif gl_internal_format == 0x8058: decoded = data[:width*height*4]
    else: raise Exception("Unsupported KTX")
    img = Image.frombytes("RGBA", (width, height), decoded)
    img = Image.merge("RGBA", (img.split()[2], img.split()[1], img.split()[0], img.split()[3])).transpose(Image.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()

def decode_base64_url(encoded_str):
    if not encoded_str:
        return ""
    encoded_str = unquote(encoded_str).strip()
    if encoded_str.startswith(('http://', 'https://')):
        return encoded_str
    try:
        encoded_bytes = encoded_str.encode("ascii")
        encoded_bytes += b"=" * (-len(encoded_bytes) % 4)
        decoded_bytes = base64.b64decode(encoded_bytes, altchars=b"-_", validate=True)
        decoded_url = decoded_bytes.decode("utf-8").strip()
        if decoded_url.startswith(("http://", "https://")):
            return decoded_url
    except (ValueError, UnicodeDecodeError, UnicodeEncodeError):
        pass
    return encoded_str

def download_bundle_from_url(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read()
    except Exception as e:
        print(f"Failed to download bundle from {url}: {e}")
    return None

def extract_from_bytes(decompressed_data: bytes, filename: str):
    extracted_list = []
    json_manifest = []
    bundle_info = unityfs_encryption_info(decompressed_data)
    
    if decompressed_data.startswith(b'\xABKTX 11\xBB\r\n\x1A\n'):
        try:
            png = convert_ktx_to_png_fallback(decompressed_data)
            name = os.path.splitext(filename)[0] + ".png"
            extracted_list.append({
                'index': 0, 
                'name': name, 
                'zip_path': f"Textures/{name}", 
                'bytes': png, 
                'label': "KTX Image"
            })
            json_manifest.append({
                'index': 0, 
                'name': name, 
                'path': f"Textures/{name}", 
                'label': "KTX Image",
                'size': len(png),
                'data': base64.b64encode(png).decode('utf-8')
            })
            return extracted_list, json_manifest, None, [], set()
        except:
            pass

    load_error = None
    texture_metadata = scan_streamed_etc1_textures(decompressed_data)
    recovered_stream_textures = set()
    try:
        configure_assetbundle_decrypt_key()
        env = load_unity_bundle(decompressed_data)
        counter = 0
        seen_md5 = set()
        for obj in env.objects:
            res = process_object_unrestricted(obj, decompressed_data)
            if res:
                fname, fbytes, zpath, tlabel = res
                h = hashlib.md5(fbytes).hexdigest()
                if h not in seen_md5:
                    seen_md5.add(h)
                    b64_data = base64.b64encode(fbytes).decode('utf-8')
                    extracted_list.append({
                        'index': counter, 
                        'name': fname, 
                        'zip_path': zpath, 
                        'bytes': fbytes, 
                        'label': tlabel
                    })
                    json_manifest.append({
                        'index': counter, 
                        'name': fname, 
                        'path': zpath, 
                        'label': tlabel,
                        'size': len(fbytes),
                        'data': b64_data
                    })
                    counter += 1

        # The UnityFS 0x200 flag blocks UnityPy's normal bundle parser, but
        # after the compatibility retry it can still expose readable .resS
        # resource streams. Recover ETC1 only from those named streams at the
        # exact offsets and sizes recorded by each Texture2D object.
        streamed_assets, recovered_stream_textures = recover_texture_assets_from_bundle_streams(env, texture_metadata)
        for asset in streamed_assets:
            asset["index"] = counter
            json_manifest.append({
                "index": counter,
                "name": asset["name"],
                "path": asset["zip_path"],
                "label": asset["label"],
                "size": len(asset["bytes"]),
                "data": base64.b64encode(asset["bytes"]).decode("ascii"),
            })
            extracted_list.append(asset)
            counter += 1
        
        if hasattr(env, 'objects'):
            env.objects.clear()
        del env
        gc.collect()
    except Exception as e:
        load_error = str(e)
        print(f"Error in UnityPy extraction: {e}")

    if not extracted_list and bundle_info and bundle_info["unitycn_encrypted"]:
        load_error = "BundleFile is encrypted; UnityCN payload blocks were not decoded"
    if not extracted_list and load_error:
        print(f"UnityPy could not parse {filename}: {load_error}")
        
    if texture_metadata and not load_error and decompressed_data.startswith(b"UnityFS\0"):
        # UnityPy can swallow the per-entry UnityCN decryption error and return
        # an empty environment after the compatibility retry. Preserve the
        # failure state so API callers receive the streamed-texture diagnostics.
        load_error = "BundleFile is encrypted; UnityCN payload blocks were not decoded"
    return extracted_list, json_manifest, load_error, texture_metadata, recovered_stream_textures

def merge_recovered_assets(extracted_list, json_manifest, recovered_list, recovered_manifest):
    """Add recovered stream assets even when the bundle also yielded other files."""
    known = {hashlib.md5(item["bytes"]).hexdigest() for item in extracted_list}
    next_index = max((item.get("index", -1) for item in extracted_list), default=-1) + 1
    for asset in recovered_list:
        digest = hashlib.md5(asset["bytes"]).hexdigest()
        if digest in known:
            continue
        known.add(digest)
        asset["index"] = next_index
        next_index += 1
        extracted_list.append(asset)
        json_manifest.append({
            "index": asset["index"],
            "name": asset["name"],
            "path": asset["zip_path"],
            "label": asset.get("label", "Recovered Asset"),
            "size": len(asset["bytes"]),
            "data": base64.b64encode(asset["bytes"]).decode("ascii"),
        })
    return extracted_list, json_manifest

@app.route('/api/extract', methods=['GET', 'POST'])
def handle_universal_extraction_pipeline():
    global GLOBAL_CACHE_REGISTRY
    
    # Check parameters
    bundle_url_param = request.args.get('bundle_url', '') or request.form.get('bundle_url', '')
    download_type = request.args.get('download_type', '')
    
    # Decode bundle url
    bundle_url = decode_base64_url(bundle_url_param)
    
    # Stateless URL Mode
    if bundle_url:
        raw_bytes = download_bundle_from_url(bundle_url)
        if not raw_bytes:
            return jsonify({"error": f"Failed to download bundle from url: {bundle_url}"}), 400
        
        filename = os.path.basename(bundle_url.split('?')[0]) or "assets"
        decompressed_data = decompress_stream(raw_bytes)
        bundle_info = unityfs_encryption_info(decompressed_data)
        
        extracted_list, json_manifest, load_error, texture_metadata, recovered_stream_textures = extract_from_bytes(decompressed_data, filename)
        unresolved_textures = []
        if texture_metadata:
            recovered_list, recovered_manifest, unresolved_textures = recover_texture_assets_from_base_apk(texture_metadata, recovered_stream_textures)
            if recovered_list:
                extracted_list, json_manifest = merge_recovered_assets(extracted_list, json_manifest, recovered_list, recovered_manifest)
        if not extracted_list:
            signature = decompressed_data[:8].decode("ascii", errors="replace").rstrip("\x00")
            if "BundleFile is encrypted" in (load_error or ""):
                return jsonify(protected_bundle_error(filename, decompressed_data, texture_metadata)), 422
            return jsonify({"error": "Bundle parsed, but no supported assets were recovered. Extraction may be incomplete if this format contains unsupported objects.", "code": "no_supported_assets_recovered", "format": signature or "unknown", "extraction_complete": False}), 422
            
        if download_type == 'zip':
            mode = request.args.get('mode', 'normal')
            filter_indices = request.args.get('indices', '')
            target_list = extracted_list
            
            if filter_indices:
                idx_list = [int(i) for i in filter_indices.split(',') if i.isdigit()]
                target_list = [item for item in target_list if item['index'] in idx_list]

            zip_io = io.BytesIO()
            with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in target_list:
                    path = item['zip_path'] if mode == 'grouped' else item['name']
                    zf.writestr(path, item['bytes'])
            zip_io.seek(0)
            
            clean_name = re.split(r'[-.]', filename)[0]
            return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=f"{clean_name}[Extracted].zip")
            
        elif download_type == 'single':
            file_idx = int(request.args.get('file_index', -1))
            if file_idx < 0 or file_idx >= len(extracted_list):
                return jsonify({"error": f"Invalid file index: {file_idx}"}), 400
            item = extracted_list[file_idx]
            return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])
            
        # Return stateless file list (manifest)
        for item in json_manifest:
            idx = item['index']
            item['url'] = f"/api/extract?bundle_url={bundle_url_param}&download_type=single&file_index={idx}"
            item['preview_url'] = f"/api/extract?bundle_url={bundle_url_param}&download_type=single&file_index={idx}"
            
        response_data = {"files": json_manifest}
        if bundle_info and bundle_info["unitycn_encrypted"]:
            response_data.update({"partial": True, "extraction_complete": False, "code": "unitycn_payload_partially_unavailable", "content_hint": infer_bundle_content(filename)})
        if texture_metadata:
            response_data.update({
                "partial": bool(unresolved_textures) or bool(bundle_info and bundle_info["unitycn_encrypted"]),
                "detected_textures": texture_metadata,
                "unresolved_textures": unresolved_textures,
            })
        return jsonify(response_data)

    # Stateful Upload Mode (for main web UI)
    if download_type == 'zip':
        if not GLOBAL_CACHE_REGISTRY.get('extracted'): return jsonify({"error": "No cache"}), 400
        mode = request.args.get('mode', 'normal')
        filter_indices = request.args.get('indices', '')
        target_list = GLOBAL_CACHE_REGISTRY['extracted']
        
        if filter_indices:
            idx_list = [int(i) for i in filter_indices.split(',') if i.isdigit()]
            target_list = [item for item in target_list if item['index'] in idx_list]

        zip_io = io.BytesIO()
        with zipfile.ZipFile(zip_io, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in target_list:
                path = item['zip_path'] if mode == 'grouped' else item['name']
                zf.writestr(path, item['bytes'])
        zip_io.seek(0)
        
        orig_filename = GLOBAL_CACHE_REGISTRY.get('original_name', 'assets')
        clean_name = re.split(r'[-.]', orig_filename)[0]
        return send_file(zip_io, mimetype='application/zip', as_attachment=True, download_name=f"{clean_name}[Extracted].zip")

    elif download_type == 'single':
        file_idx = int(request.args.get('file_index', -1))
        if not GLOBAL_CACHE_REGISTRY.get('extracted') or file_idx < 0 or file_idx >= len(GLOBAL_CACHE_REGISTRY['extracted']):
            return jsonify({"error": "Index error"}), 400
        item = GLOBAL_CACHE_REGISTRY['extracted'][file_idx]
        return send_file(io.BytesIO(item['bytes']), mimetype='application/octet-stream', as_attachment=True, download_name=item['name'])

    if 'asset_bundle' not in request.files: return jsonify({"error": "No file"}), 400
    try:
        uploaded_file = request.files['asset_bundle']
        GLOBAL_CACHE_REGISTRY['original_name'] = uploaded_file.filename
        raw_bytes = uploaded_file.read()
        decompressed_data = decompress_stream(raw_bytes)
        bundle_info = unityfs_encryption_info(decompressed_data)
        
        extracted_list, json_manifest, load_error, texture_metadata, recovered_stream_textures = extract_from_bytes(decompressed_data, uploaded_file.filename)
        unresolved_textures = []
        if texture_metadata:
            recovered_list, recovered_manifest, unresolved_textures = recover_texture_assets_from_base_apk(texture_metadata, recovered_stream_textures)
            if recovered_list:
                extracted_list, json_manifest = merge_recovered_assets(extracted_list, json_manifest, recovered_list, recovered_manifest)
        if not extracted_list:
            if "BundleFile is encrypted" in (load_error or ""):
                return jsonify(protected_bundle_error(uploaded_file.filename, decompressed_data, texture_metadata)), 422
            return jsonify({"error": "Bundle parsed, but no supported assets were recovered. Extraction may be incomplete if this format contains unsupported objects.", "code": "no_supported_assets_recovered", "extraction_complete": False}), 422
            
        GLOBAL_CACHE_REGISTRY['extracted'] = extracted_list
        
        for item in json_manifest:
            idx = item['index']
            item['url'] = f"/api/extract?download_type=single&file_index={idx}"
            item['preview_url'] = f"/api/extract?download_type=single&file_index={idx}"
            
        response_data = {"files": json_manifest}
        if bundle_info and bundle_info["unitycn_encrypted"]:
            response_data.update({"partial": True, "extraction_complete": False, "code": "unitycn_payload_partially_unavailable", "content_hint": infer_bundle_content(uploaded_file.filename)})
        if texture_metadata:
            response_data.update({
                "partial": bool(unresolved_textures) or bool(bundle_info and bundle_info["unitycn_encrypted"]),
                "detected_textures": texture_metadata,
                "unresolved_textures": unresolved_textures,
            })
        return jsonify(response_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_ui_layout(path):
    try:
        with open(HTML_PATH, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e: return f"Error: {str(e)}", 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=10000, debug=True)
