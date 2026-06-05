import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from http.server import BaseHTTPRequestHandler
import UnityPy
import lz4.frame

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
                if match != -1: raw = raw_env_data[match:match+30_000_000] # Kept safe for serverless ram
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

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            
            # Simple handling of multipart data boundaries if coming raw
            # To stay within memory thresholds, we process the stream efficiently
            body = self.rfile.read(content_length)
            
            # Extract raw bytes from multi-part boundary if present
            if b'filename=' in body:
                header_end = body.find(b'\r\n\r\n') + 4
                footer_start = body.rfind(b'\r\n--')
                raw_bytes = body[header_end:footer_start]
            else:
                raw_bytes = body

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
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No recognizable Unity assets found in file."}).encode())
                return

            # Return Compiled ZIP File
            zip_io.seek(0)
            data_to_send = zip_io.read()

            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', 'attachment; filename="extracted_assets.zip"')
            self.send_header('Content-Length', str(len(data_to_send)))
            self.end_headers()
            self.wfile.write(data_to_send)

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())