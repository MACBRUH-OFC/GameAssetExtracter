import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
import base64
import sys
from flask import Flask, request, jsonify, send_file, Response

# Vercel serverless requires this
app = Flask(__name__)

# Disable file watcher for Vercel
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = False

# Supported asset types
SUPPORTED_TYPES = [
    "Texture2D", "Sprite", "AudioClip", "VideoClip", "TextAsset",
    "Font", "Mesh", "Shader", "MonoBehaviour"
]

# Maximum file size (10MB for Vercel free tier)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Global cache (will reset per request in serverless)
GLOBAL_CACHE = {}


def decompress_stream(data: bytes) -> bytes:
    """Decompress gzip/zlib compressed data."""
    try:
        if len(data) > 2 and data[0:2] == b'\x1f\x8b':
            return decompress_stream(gzip.decompress(data))
        if len(data) > 2 and data[0] == 0x78 and data[1] in [0x01, 0x9C, 0xDA]:
            return decompress_stream(zlib.decompress(data))
    except Exception:
        pass
    return data


def safe_filename(name: str) -> str:
    """Create safe filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return name[:50] or "unnamed"


def extract_asset(obj, env_data: bytes, idx: int):
    """Extract single asset from Unity object."""
    try:
        obj_type = obj.type.name
        if obj_type not in SUPPORTED_TYPES:
            return None

        data = obj.read()
        
        # Get name
        asset_name = None
        if hasattr(obj, 'container') and obj.container:
            asset_name = os.path.splitext(os.path.basename(obj.container))[0]
        if not asset_name:
            for attr in ["m_Name", "name"]:
                if hasattr(data, attr):
                    val = getattr(data, attr, "")
                    if val and isinstance(val, str) and val.strip():
                        asset_name = val.strip()
                        break
        if not asset_name:
            asset_name = f"{obj_type}_{obj.path_id}"

        asset_name = safe_filename(asset_name)

        # Texture extraction
        if obj_type in ["Texture2D", "Sprite"] and hasattr(data, 'image'):
            try:
                img = data.image
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    return f"{asset_name}.png", buf.getvalue(), f"Textures/{asset_name}.png"
            except Exception:
                pass

        # Audio extraction
        elif obj_type == "AudioClip":
            try:
                if hasattr(data, 'samples') and data.samples:
                    for audio_name, audio_data in data.samples.items():
                        if audio_data and len(audio_data) > 1000:
                            ext = '.wav' if audio_data[:4] == b'RIFF' else '.ogg'
                            return f"{safe_filename(audio_name)}{ext}", audio_data, f"Audio/{safe_filename(audio_name)}{ext}"
                raw = obj.get_raw_data()
                if raw and len(raw) > 1000:
                    ext = '.wav' if raw[:4] == b'RIFF' else '.ogg'
                    return f"{asset_name}{ext}", raw, f"Audio/{asset_name}{ext}"
            except Exception:
                pass

        # Video extraction
        elif obj_type == "VideoClip":
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 10000:
                    if b'moov' in raw or b'ftyp' in raw:
                        return f"{asset_name}.mp4", raw[:5000000], f"Video/{asset_name}.mp4"
                # Search in env data
                for sig in [b'ftypmp4', b'moov']:
                    pos = env_data.find(sig)
                    if pos != -1:
                        start = max(0, pos - 100)
                        video_data = env_data[start:start + 5000000]
                        return f"{asset_name}.mp4", video_data, f"Video/{asset_name}.mp4"
            except Exception:
                pass

        # Text extraction
        elif obj_type == "TextAsset":
            try:
                text = ""
                if hasattr(data, 'm_Script'):
                    text = data.m_Script
                if text:
                    if isinstance(text, bytes):
                        text = text.decode('utf-8', errors='replace')
                    ext = '.json' if text.strip().startswith(('{', '[')) else '.txt'
                    return f"{asset_name}{ext}", text.encode('utf-8'), f"Text/{asset_name}{ext}"
            except Exception:
                pass

        # Font extraction
        elif obj_type == "Font":
            try:
                if hasattr(data, 'm_FontData') and data.m_FontData:
                    font_bytes = bytes(data.m_FontData) if isinstance(data.m_FontData, (bytes, bytearray)) else data.m_FontData
                    if font_bytes and len(font_bytes) > 100:
                        ext = '.ttf' if font_bytes[:4] in [b'\x00\x01\x00\x00', b'OTTO'] else '.font'
                        return f"{asset_name}{ext}", font_bytes, f"Fonts/{asset_name}{ext}"
            except Exception:
                pass

        # Shader extraction
        elif obj_type == "Shader":
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 100:
                    return f"{asset_name}.shader", raw[:500000], f"Shaders/{asset_name}.shader"
            except Exception:
                pass

        # Mesh extraction (raw data)
        elif obj_type == "Mesh":
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 100:
                    return f"{asset_name}.mesh", raw[:500000], f"Meshes/{asset_name}.mesh"
            except Exception:
                pass

        # MonoBehaviour as JSON
        elif obj_type == "MonoBehaviour":
            try:
                info = {"type": obj_type, "name": asset_name, "path_id": obj.path_id}
                return f"{asset_name}.json", json.dumps(info, indent=2).encode('utf-8'), f"Scripts/{asset_name}.json"
            except Exception:
                pass

    except Exception as e:
        pass
    
    return None


# HTML interface (embedded for Vercel)
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unity Asset Extractor</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #0a0c15; font-family: system-ui, -apple-system, sans-serif; }
        .log-line { padding: 4px 8px; border-bottom: 1px solid #1a1e2a; font-size: 11px; font-family: monospace; color: #9ca3af; }
        .asset-item { transition: all 0.15s ease; cursor: pointer; }
        .asset-item:hover { background: #1a1f2e; transform: translateX(2px); }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: #11141f; }
        ::-webkit-scrollbar-thumb { background: #2d3748; border-radius: 4px; }
    </style>
</head>
<body class="text-gray-200 min-h-screen">
    <div class="max-w-6xl mx-auto p-5">
        <header class="mb-6">
            <h1 class="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">Unity Asset Extractor</h1>
            <p class="text-gray-400 text-sm mt-1">Extract textures, audio, video, text, fonts, shaders from Unity bundles</p>
        </header>

        <div class="grid md:grid-cols-2 gap-5">
            <!-- Left Panel -->
            <div class="bg-[#0f1119] rounded-xl border border-gray-800 p-5">
                <div id="dropzone" class="border-2 border-dashed border-gray-700 rounded-lg p-8 text-center cursor-pointer hover:border-blue-500 transition">
                    <input type="file" id="fileInput" class="hidden" accept=".unity3d,.assetbundle,.assets,.bundle,.dat">
                    <svg class="w-8 h-8 mx-auto text-gray-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
                    <p class="text-sm text-gray-400">Drop Unity bundle or <span class="text-blue-400 underline">browse</span></p>
                    <p class="text-xs text-gray-600 mt-1">Max 10MB (.unity3d, .assetbundle)</p>
                </div>

                <div id="fileInfo" class="hidden mt-4 p-3 bg-[#0a0c15] rounded-lg border border-gray-800 text-sm">
                    <div class="flex justify-between"><span class="text-gray-500">File:</span><span id="fileName" class="font-mono text-xs">-</span></div>
                    <div class="flex justify-between mt-1"><span class="text-gray-500">Size:</span><span id="fileSize">-</span></div>
                </div>

                <button id="extractBtn" disabled class="w-full mt-4 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 disabled:from-gray-700 disabled:to-gray-700 disabled:cursor-not-allowed text-white font-semibold py-2.5 rounded-lg transition">Extract Assets</button>

                <div id="progress" class="hidden mt-4">
                    <div class="flex justify-between text-xs mb-1"><span id="status">Processing...</span><span id="percent">0%</span></div>
                    <div class="h-1 bg-gray-700 rounded-full overflow-hidden"><div id="bar" class="h-full bg-blue-500 transition-all" style="width:0%"></div></div>
                </div>

                <div class="mt-5">
                    <div class="flex justify-between items-center mb-2"><span class="text-xs text-gray-500 uppercase tracking-wide">Activity Log</span><button id="clearLogs" class="text-xs text-gray-600 hover:text-gray-400">Clear</button></div>
                    <div id="logContainer" class="bg-[#0a0c15] rounded-lg border border-gray-800 h-40 overflow-y-auto"></div>
                </div>
            </div>

            <!-- Right Panel -->
            <div class="bg-[#0f1119] rounded-xl border border-gray-800 p-5 flex flex-col">
                <div class="flex justify-between items-center mb-3">
                    <div><h2 class="font-semibold">Extracted Assets</h2><span id="assetCount" class="text-xs text-gray-500">0 items</span></div>
                    <button id="downloadAllBtn" class="hidden px-3 py-1.5 bg-emerald-800/50 hover:bg-emerald-700 rounded-lg text-xs transition">Download All ZIP</button>
                </div>
                <div id="assetList" class="flex-1 overflow-y-auto max-h-96 space-y-1"></div>
                <div id="previewArea" class="mt-4 pt-4 border-t border-gray-800">
                    <div class="text-xs text-gray-500 mb-2">Preview</div>
                    <div id="previewContent" class="bg-[#0a0c15] rounded-lg border border-gray-800 p-3 min-h-[150px] flex items-center justify-center text-gray-500 text-sm">Select an asset to preview</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const dropzone = document.getElementById('dropzone');
        const fileInput = document.getElementById('fileInput');
        const extractBtn = document.getElementById('extractBtn');
        const downloadAllBtn = document.getElementById('downloadAllBtn');
        const assetListDiv = document.getElementById('assetList');
        const logContainer = document.getElementById('logContainer');
        const progressDiv = document.getElementById('progress');
        const statusSpan = document.getElementById('status');
        const percentSpan = document.getElementById('percent');
        const bar = document.getElementById('bar');
        const fileNameSpan = document.getElementById('fileName');
        const fileSizeSpan = document.getElementById('fileSize');
        const fileInfoDiv = document.getElementById('fileInfo');
        const assetCountSpan = document.getElementById('assetCount');
        const previewContent = document.getElementById('previewContent');
        const clearLogsBtn = document.getElementById('clearLogs');

        let selectedFile = null;
        let currentAssets = [];

        function addLog(msg, isError = false) {
            const div = document.createElement('div');
            div.className = 'log-line';
            div.style.color = isError ? '#f87171' : '#9ca3af';
            div.textContent = msg;
            logContainer.appendChild(div);
            div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        clearLogsBtn.onclick = () => { logContainer.innerHTML = '<div class="log-line text-gray-600">Log cleared.</div>'; };

        dropzone.ondragover = (e) => { e.preventDefault(); dropzone.classList.add('border-blue-500', 'bg-blue-500/10'); };
        dropzone.ondragleave = () => dropzone.classList.remove('border-blue-500', 'bg-blue-500/10');
        dropzone.ondrop = (e) => { e.preventDefault(); dropzone.classList.remove('border-blue-500', 'bg-blue-500/10'); if(e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); };
        dropzone.onclick = () => fileInput.click();
        fileInput.onchange = (e) => { if(e.target.files[0]) handleFile(e.target.files[0]); };

        function handleFile(file) {
            if(file.size > 10 * 1024 * 1024) {
                alert('File too large. Max 10MB for Vercel deployment.');
                return;
            }
            selectedFile = file;
            fileNameSpan.textContent = file.name;
            fileSizeSpan.textContent = (file.size / 1024).toFixed(1) + ' KB';
            fileInfoDiv.classList.remove('hidden');
            extractBtn.disabled = false;
            addLog(`Loaded: ${file.name}`);
        }

        extractBtn.onclick = async () => {
            if(!selectedFile) return;
            extractBtn.disabled = true;
            progressDiv.classList.remove('hidden');
            bar.style.width = '0%';
            percentSpan.textContent = '0%';
            statusSpan.textContent = 'Uploading...';
            
            const formData = new FormData();
            formData.append('asset_bundle', selectedFile);
            
            try {
                statusSpan.textContent = 'Extracting...';
                bar.style.width = '50%';
                percentSpan.textContent = '50%';
                
                const res = await fetch('/api/extract', { method: 'POST', body: formData });
                bar.style.width = '90%';
                percentSpan.textContent = '90%';
                
                if(!res.ok) {
                    const err = await res.json();
                    throw new Error(err.error || 'Extraction failed');
                }
                
                const data = await res.json();
                bar.style.width = '100%';
                percentSpan.textContent = '100%';
                statusSpan.textContent = 'Complete!';
                
                currentAssets = data.files || [];
                addLog(`✅ Extracted ${currentAssets.length} assets`);
                renderAssetList(currentAssets);
                assetCountSpan.textContent = `${currentAssets.length} items`;
                downloadAllBtn.classList.remove('hidden');
                
                setTimeout(() => { progressDiv.classList.add('hidden'); }, 1500);
            } catch(err) {
                addLog(`❌ Error: ${err.message}`, true);
                statusSpan.textContent = 'Failed';
                bar.classList.add('bg-red-500');
                setTimeout(() => { progressDiv.classList.add('hidden'); bar.classList.remove('bg-red-500'); }, 2000);
            } finally {
                extractBtn.disabled = false;
            }
        };

        function renderAssetList(assets) {
            assetListDiv.innerHTML = '';
            assets.forEach((asset, idx) => {
                const category = asset.path.split('/')[0];
                const colors = {
                    'Textures': 'text-blue-400 bg-blue-950/40',
                    'Audio': 'text-emerald-400 bg-emerald-950/40',
                    'Video': 'text-amber-400 bg-amber-950/40',
                    'Text': 'text-cyan-400 bg-cyan-950/40',
                    'Fonts': 'text-pink-400 bg-pink-950/40',
                    'Shaders': 'text-purple-400 bg-purple-950/40'
                };
                const badgeColor = colors[category] || 'text-gray-400 bg-gray-800';
                
                const div = document.createElement('div');
                div.className = 'asset-item flex items-center justify-between p-2 rounded-lg bg-[#0a0c15] border border-gray-800 hover:border-gray-700';
                div.innerHTML = `
                    <div class="flex items-center gap-2 flex-1 min-w-0" onclick="previewAsset(${idx})">
                        <span class="text-[9px] font-mono px-1.5 py-0.5 rounded ${badgeColor}">${category}</span>
                        <span class="text-xs truncate">${asset.name}</span>
                    </div>
                    <button class="download-single text-gray-500 hover:text-white p-1" data-idx="${idx}" data-name="${asset.name}">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-3-3m3 3l3-3"/></svg>
                    </button>
                `;
                div.querySelector('.download-single').onclick = (e) => {
                    e.stopPropagation();
                    downloadAsset(asset.name, idx);
                };
                assetListDiv.appendChild(div);
            });
        }

        window.previewAsset = async (idx) => {
            previewContent.innerHTML = '<div class="animate-pulse">Loading preview...</div>';
            try {
                const res = await fetch(`/api/extract?download_type=single&file_index=${idx}`, { method: 'POST' });
                if(!res.ok) throw new Error('Preview failed');
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const asset = currentAssets[idx];
                const category = asset.path.split('/')[0];
                
                if(category === 'Textures') {
                    previewContent.innerHTML = `<img src="${url}" class="max-h-32 max-w-full rounded-lg mx-auto">`;
                } else if(category === 'Audio') {
                    previewContent.innerHTML = `<audio controls class="w-full"><source src="${url}"></audio>`;
                } else if(category === 'Video') {
                    previewContent.innerHTML = `<video controls class="max-h-32 w-full"><source src="${url}"></video>`;
                } else if(category === 'Text') {
                    const text = await blob.text();
                    previewContent.innerHTML = `<pre class="text-xs overflow-auto max-h-32">${text.substring(0, 500)}${text.length > 500 ? '...' : ''}</pre>`;
                } else {
                    previewContent.innerHTML = `<a href="${url}" download="${asset.name}" class="text-blue-400 underline">Download ${asset.name}</a>`;
                }
            } catch(e) {
                previewContent.innerHTML = `<div class="text-red-400 text-xs">Preview error: ${e.message}</div>`;
            }
        };

        async function downloadAsset(name, idx) {
            try {
                const res = await fetch(`/api/extract?download_type=single&file_index=${idx}`, { method: 'POST' });
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = name;
                a.click();
                URL.revokeObjectURL(url);
                addLog(`Downloaded: ${name}`);
            } catch(e) {
                alert('Download failed');
            }
        }

        downloadAllBtn.onclick = async () => {
            try {
                addLog('Creating ZIP archive...');
                const res = await fetch('/api/extract?download_type=zip', { method: 'POST' });
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `assets_${Date.now()}.zip`;
                a.click();
                URL.revokeObjectURL(url);
                addLog('ZIP download started');
            } catch(e) {
                alert('ZIP creation failed');
            }
        };
    </script>
</body>
</html>'''


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_html(path):
    """Serve HTML interface."""
    return HTML_TEMPLATE


@app.route('/api/extract', methods=['POST', 'OPTIONS'])
def extract():
    """Main extraction endpoint."""
    if request.method == 'OPTIONS':
        return '', 200
    
    download_type = request.args.get('download_type', '')
    
    # ZIP download
    if download_type == 'zip':
        if not GLOBAL_CACHE.get('assets'):
            return jsonify({'error': 'No assets in cache'}), 400
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for asset in GLOBAL_CACHE['assets']:
                zf.writestr(asset['path'], asset['data'])
        zip_buffer.seek(0)
        return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='extracted_assets.zip')
    
    # Single download
    if download_type == 'single':
        idx = request.args.get('file_index', type=int)
        if idx is None or not GLOBAL_CACHE.get('assets') or idx >= len(GLOBAL_CACHE['assets']):
            return jsonify({'error': 'Invalid index'}), 400
        asset = GLOBAL_CACHE['assets'][idx]
        return send_file(io.BytesIO(asset['data']), mimetype='application/octet-stream', as_attachment=True, download_name=asset['name'])
    
    # Main extraction
    if 'asset_bundle' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['asset_bundle']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    
    try:
        raw_data = file.read()
        if len(raw_data) > MAX_FILE_SIZE:
            return jsonify({'error': f'File exceeds {MAX_FILE_SIZE//1024//1024}MB limit'}), 400
        
        decompressed = decompress_stream(raw_data)
        
        # Try to load with UnityPy
        try:
            import UnityPy
            env = UnityPy.load(decompressed)
        except Exception as e:
            return jsonify({'error': f'Invalid Unity bundle: {str(e)}'}), 400
        
        assets = []
        manifest = []
        
        for idx, obj in enumerate(env.objects):
            result = extract_asset(obj, decompressed, idx)
            if result:
                name, data, path = result
                # Deduplicate by content hash
                content_hash = hashlib.md5(data).hexdigest()
                if not any(a['hash'] == content_hash for a in assets):
                    assets.append({
                        'name': name,
                        'data': data,
                        'path': path,
                        'hash': content_hash
                    })
                    manifest.append({
                        'index': len(assets) - 1,
                        'name': name,
                        'path': path
                    })
        
        # Clean up
        del env
        import gc
        gc.collect()
        
        GLOBAL_CACHE['assets'] = assets
        
        if not manifest:
            return jsonify({'error': 'No supported assets found'}), 400
        
        return jsonify({'files': manifest, 'count': len(manifest)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


# Vercel requires this handler
handler = app