import os
import io
import json
import gzip
import zlib
import zipfile
import re
import hashlib
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# Cache for extracted assets (per request in serverless, but persists briefly)
ASSET_CACHE = {}

# HTML content embedded directly
HTML_CONTENT = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Unity Asset Extractor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0a0c15 0%, #0f1119 100%);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
            color: #e2e8f0;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 {
            font-size: 2rem;
            background: linear-gradient(135deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }
        .header p { color: #64748b; margin-top: 8px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .card {
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            border: 1px solid #1e293b;
            padding: 24px;
        }
        .dropzone {
            border: 2px dashed #334155;
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
        }
        .dropzone:hover { border-color: #3b82f6; background: rgba(59, 130, 246, 0.05); }
        .btn {
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            margin-top: 16px;
            transition: opacity 0.2s;
        }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-secondary {
            background: linear-gradient(135deg, #059669, #10b981);
        }
        .file-info {
            background: #0f172a;
            border-radius: 8px;
            padding: 12px;
            margin-top: 16px;
            display: none;
        }
        .file-info.show { display: block; }
        .log {
            background: #0f172a;
            border-radius: 8px;
            padding: 12px;
            height: 200px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 12px;
            margin-top: 16px;
        }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #1e293b; color: #94a3b8; }
        .log-entry.error { color: #ef4444; }
        .log-entry.success { color: #10b981; }
        .asset-list {
            max-height: 400px;
            overflow-y: auto;
        }
        .asset-item {
            background: #0f172a;
            border-radius: 8px;
            padding: 10px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .asset-item:hover { background: #1e293b; transform: translateX(4px); }
        .asset-info { display: flex; align-items: center; gap: 12px; flex: 1; }
        .badge {
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
        }
        .badge-texture { background: #3b82f6; color: white; }
        .badge-audio { background: #10b981; color: white; }
        .badge-video { background: #f59e0b; color: white; }
        .badge-text { background: #06b6d4; color: white; }
        .badge-font { background: #ec4899; color: white; }
        .badge-shader { background: #8b5cf6; color: white; }
        .badge-other { background: #475569; color: white; }
        .download-icon {
            opacity: 0;
            transition: opacity 0.2s;
            background: none;
            border: none;
            cursor: pointer;
            color: #94a3b8;
        }
        .asset-item:hover .download-icon { opacity: 1; }
        .preview-area {
            background: #0f172a;
            border-radius: 8px;
            padding: 16px;
            margin-top: 16px;
            min-height: 200px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .progress-bar {
            width: 100%;
            height: 4px;
            background: #1e293b;
            border-radius: 2px;
            overflow: hidden;
            margin-top: 12px;
            display: none;
        }
        .progress-bar.active { display: block; }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            width: 0%;
            transition: width 0.3s;
        }
        .status-text { font-size: 12px; color: #94a3b8; margin-top: 8px; display: none; }
        .status-text.active { display: block; }
        .flex-between { display: flex; justify-content: space-between; align-items: center; }
        .mt-4 { margin-top: 16px; }
        .text-sm { font-size: 14px; }
        .text-xs { font-size: 12px; }
        .text-gray { color: #64748b; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎮 Unity Asset Extractor</h1>
            <p>Extract Textures, Audio, Video, Text, Fonts, Shaders from Unity bundles</p>
        </div>

        <div class="grid">
            <!-- Left Panel -->
            <div class="card">
                <h3 style="margin-bottom: 16px;">📦 Upload Bundle</h3>
                <div class="dropzone" id="dropzone">
                    <div>📁 Drag & Drop or Click to Browse</div>
                    <div class="text-xs text-gray" style="margin-top: 8px;">Supported: .unity3d, .assetbundle, .assets (Max 10MB)</div>
                    <input type="file" id="fileInput" accept=".unity3d,.assetbundle,.assets,.bundle,.dat" style="display: none;">
                </div>

                <div class="file-info" id="fileInfo">
                    <div class="flex-between"><span class="text-gray">File:</span><span id="fileName">-</span></div>
                    <div class="flex-between mt-4"><span class="text-gray">Size:</span><span id="fileSize">-</span></div>
                </div>

                <button class="btn" id="extractBtn" disabled>🚀 Extract Assets</button>

                <div class="progress-bar" id="progressBar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="status-text" id="statusText"></div>

                <div class="flex-between" style="margin-top: 24px; margin-bottom: 8px;">
                    <span class="text-sm">📋 Activity Log</span>
                    <button id="clearLogs" style="background: none; border: none; color: #64748b; cursor: pointer; font-size: 12px;">Clear</button>
                </div>
                <div class="log" id="logContainer">
                    <div class="log-entry">Ready — Upload a Unity bundle to begin</div>
                </div>
            </div>

            <!-- Right Panel -->
            <div class="card">
                <div class="flex-between">
                    <div>
                        <h3>📋 Extracted Assets</h3>
                        <div class="text-xs text-gray" id="assetCount">0 items</div>
                    </div>
                    <button class="btn-secondary" id="downloadAllBtn" style="width: auto; padding: 8px 16px; margin-top: 0; display: none;">📦 Download All ZIP</button>
                </div>
                <div class="asset-list" id="assetList">
                    <div class="text-gray" style="text-align: center; padding: 40px;">No assets extracted yet</div>
                </div>
                <div class="preview-area" id="previewArea">
                    <div class="text-gray">Select an asset to preview</div>
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
        const fileInfo = document.getElementById('fileInfo');
        const fileName = document.getElementById('fileName');
        const fileSize = document.getElementById('fileSize');
        const progressBar = document.getElementById('progressBar');
        const progressFill = document.getElementById('progressFill');
        const statusText = document.getElementById('statusText');
        const assetCountSpan = document.getElementById('assetCount');
        const previewArea = document.getElementById('previewArea');
        const clearLogsBtn = document.getElementById('clearLogs');

        let selectedFile = null;
        let currentAssets = [];

        function addLog(msg, type = 'info') {
            const div = document.createElement('div');
            div.className = `log-entry ${type === 'error' ? 'error' : (type === 'success' ? 'success' : '')}`;
            div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
            logContainer.appendChild(div);
            div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        clearLogsBtn.onclick = () => {
            logContainer.innerHTML = '<div class="log-entry">Log cleared</div>';
        };

        dropzone.onclick = () => fileInput.click();
        dropzone.ondragover = (e) => { e.preventDefault(); dropzone.style.borderColor = '#3b82f6'; };
        dropzone.ondragleave = () => dropzone.style.borderColor = '#334155';
        dropzone.ondrop = (e) => {
            e.preventDefault();
            dropzone.style.borderColor = '#334155';
            if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
        };
        fileInput.onchange = (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); };

        function handleFile(file) {
            if (file.size > 10 * 1024 * 1024) {
                addLog(`File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB (max 10MB)`, 'error');
                return;
            }
            selectedFile = file;
            fileName.textContent = file.name;
            fileSize.textContent = `${(file.size / 1024).toFixed(1)} KB`;
            fileInfo.classList.add('show');
            extractBtn.disabled = false;
            addLog(`Loaded: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`, 'success');
        }

        extractBtn.onclick = async () => {
            if (!selectedFile) return;

            extractBtn.disabled = true;
            progressBar.classList.add('active');
            statusText.classList.add('active');
            progressFill.style.width = '0%';
            statusText.textContent = 'Uploading file...';

            const formData = new FormData();
            formData.append('asset_bundle', selectedFile);

            try {
                progressFill.style.width = '30%';
                statusText.textContent = 'Processing bundle...';

                const response = await fetch('/api/extract', {
                    method: 'POST',
                    body: formData
                });

                progressFill.style.width = '80%';
                statusText.textContent = 'Extracting assets...';

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.error || 'Extraction failed');
                }

                const data = await response.json();
                currentAssets = data.files || [];
                
                progressFill.style.width = '100%';
                statusText.textContent = `Complete! Extracted ${currentAssets.length} assets`;
                addLog(`✅ Successfully extracted ${currentAssets.length} assets`, 'success');

                renderAssetList(currentAssets);
                assetCountSpan.textContent = `${currentAssets.length} items`;
                
                if (currentAssets.length > 0) {
                    downloadAllBtn.style.display = 'block';
                }

                setTimeout(() => {
                    progressBar.classList.remove('active');
                    statusText.classList.remove('active');
                }, 2000);

            } catch (err) {
                addLog(`❌ Error: ${err.message}`, 'error');
                statusText.textContent = `Error: ${err.message}`;
                setTimeout(() => {
                    progressBar.classList.remove('active');
                    statusText.classList.remove('active');
                }, 3000);
            } finally {
                extractBtn.disabled = false;
            }
        };

        function getBadgeClass(path) {
            if (path.startsWith('Textures')) return 'badge-texture';
            if (path.startsWith('Audio')) return 'badge-audio';
            if (path.startsWith('Video')) return 'badge-video';
            if (path.startsWith('Text')) return 'badge-text';
            if (path.startsWith('Fonts')) return 'badge-font';
            if (path.startsWith('Shaders')) return 'badge-shader';
            return 'badge-other';
        }

        function getCategory(path) {
            return path.split('/')[0];
        }

        function renderAssetList(assets) {
            if (!assets.length) {
                assetListDiv.innerHTML = '<div class="text-gray" style="text-align: center; padding: 40px;">No assets extracted yet</div>';
                return;
            }

            assetListDiv.innerHTML = '';
            assets.forEach((asset, index) => {
                const category = getCategory(asset.path);
                const badgeClass = getBadgeClass(asset.path);
                
                const div = document.createElement('div');
                div.className = 'asset-item';
                div.innerHTML = `
                    <div class="asset-info" onclick="previewAsset(${index})">
                        <span class="badge ${badgeClass}">${category}</span>
                        <span style="font-size: 13px; flex: 1;">${escapeHtml(asset.name)}</span>
                    </div>
                    <button class="download-icon" onclick="downloadAsset(${index}, event)" title="Download">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-3-3m3 3l3-3"/>
                        </svg>
                    </button>
                `;
                assetListDiv.appendChild(div);
            });
        }

        window.previewAsset = async (index) => {
            const asset = currentAssets[index];
            previewArea.innerHTML = '<div class="text-gray">Loading preview...</div>';
            
            try {
                const response = await fetch(`/api/extract?download_type=single&file_index=${index}`, { method: 'POST' });
                if (!response.ok) throw new Error('Preview failed');
                
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const category = getCategory(asset.path);
                
                if (category === 'Textures') {
                    previewArea.innerHTML = `<img src="${url}" style="max-width: 100%; max-height: 180px; border-radius: 8px;">`;
                } else if (category === 'Audio') {
                    previewArea.innerHTML = `<audio controls style="width: 100%;"><source src="${url}"></audio>`;
                } else if (category === 'Video') {
                    previewArea.innerHTML = `<video controls style="max-width: 100%; max-height: 180px;"><source src="${url}"></video>`;
                } else if (category === 'Text') {
                    const text = await blob.text();
                    previewArea.innerHTML = `<pre style="font-size: 11px; overflow: auto; max-height: 180px; background: #0f172a; padding: 8px; border-radius: 8px;">${escapeHtml(text.substring(0, 1000))}${text.length > 1000 ? '...' : ''}</pre>`;
                } else {
                    previewArea.innerHTML = `<div class="text-gray">Preview not available for ${category} files<br><a href="${url}" download="${asset.name}" style="color: #3b82f6;">Download ${asset.name}</a></div>`;
                }
            } catch (err) {
                previewArea.innerHTML = `<div class="text-gray" style="color: #ef4444;">Preview failed: ${err.message}</div>`;
            }
        };

        window.downloadAsset = async (index, event) => {
            event.stopPropagation();
            const asset = currentAssets[index];
            try {
                const response = await fetch(`/api/extract?download_type=single&file_index=${index}`, { method: 'POST' });
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = asset.name;
                a.click();
                URL.revokeObjectURL(url);
                addLog(`Downloaded: ${asset.name}`, 'success');
            } catch (err) {
                addLog(`Download failed: ${err.message}`, 'error');
            }
        };

        downloadAllBtn.onclick = async () => {
            try {
                addLog('Creating ZIP archive...');
                const response = await fetch('/api/extract?download_type=zip', { method: 'POST' });
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `assets_${Date.now()}.zip`;
                a.click();
                URL.revokeObjectURL(url);
                addLog('ZIP download started', 'success');
            } catch (err) {
                addLog(`ZIP creation failed: ${err.message}`, 'error');
            }
        };

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>'''

# Helper functions
def decompress_data(data):
    """Decompress gzip/zlib compressed data."""
    try:
        if len(data) > 2 and data[0:2] == b'\x1f\x8b':
            return gzip.decompress(data)
        if len(data) > 2 and data[0] == 0x78 and data[1] in [0x01, 0x9C, 0xDA]:
            return zlib.decompress(data)
    except:
        pass
    return data

def sanitize_name(name):
    """Sanitize filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    return name[:50] if name else 'unnamed'

def extract_asset(obj, env_data):
    """Extract asset from Unity object."""
    try:
        obj_type = obj.type.name
        data = obj.read()
        
        # Get name
        asset_name = None
        if hasattr(obj, 'container') and obj.container:
            asset_name = os.path.basename(obj.container).split('.')[0]
        if not asset_name:
            for attr in ['m_Name', 'name', 'm_name']:
                if hasattr(data, attr):
                    val = getattr(data, attr, '')
                    if val and isinstance(val, str) and val.strip():
                        asset_name = val.strip()
                        break
        if not asset_name:
            asset_name = f"{obj_type}_{obj.path_id}"
        
        asset_name = sanitize_name(asset_name)
        
        # Texture2D / Sprite
        if obj_type in ['Texture2D', 'Sprite'] and hasattr(data, 'image'):
            try:
                img = data.image
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    return f"{asset_name}.png", buf.getvalue(), f"Textures/{asset_name}.png"
            except:
                pass
        
        # AudioClip
        elif obj_type == 'AudioClip':
            try:
                if hasattr(data, 'samples') and data.samples:
                    for audio_name, audio_data in data.samples.items():
                        if audio_data and len(audio_data) > 100:
                            ext = '.wav' if audio_data[:4] == b'RIFF' else '.ogg'
                            return f"{sanitize_name(audio_name)}{ext}", audio_data, f"Audio/{sanitize_name(audio_name)}{ext}"
                raw = obj.get_raw_data()
                if raw and len(raw) > 100:
                    ext = '.wav' if raw[:4] == b'RIFF' else '.ogg'
                    return f"{asset_name}{ext}", raw, f"Audio/{asset_name}{ext}"
            except:
                pass
        
        # VideoClip
        elif obj_type == 'VideoClip':
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 1000:
                    if b'moov' in raw or b'ftyp' in raw:
                        return f"{asset_name}.mp4", raw[:5000000], f"Video/{asset_name}.mp4"
                # Search in environment
                for sig in [b'ftypmp4', b'moov', b'MDAT']:
                    pos = env_data.find(sig)
                    if pos != -1:
                        start = max(0, pos - 100)
                        video_data = env_data[start:start + 5000000]
                        return f"{asset_name}.mp4", video_data, f"Video/{asset_name}.mp4"
            except:
                pass
        
        # TextAsset
        elif obj_type == 'TextAsset':
            try:
                text = ''
                if hasattr(data, 'm_Script'):
                    text = data.m_Script
                if text:
                    if isinstance(text, bytes):
                        text = text.decode('utf-8', errors='replace')
                    ext = '.json' if text.strip().startswith(('{', '[')) else '.txt'
                    return f"{asset_name}{ext}", text.encode('utf-8'), f"Text/{asset_name}{ext}"
            except:
                pass
        
        # Font
        elif obj_type == 'Font':
            try:
                if hasattr(data, 'm_FontData') and data.m_FontData:
                    font_bytes = bytes(data.m_FontData) if isinstance(data.m_FontData, (bytes, bytearray)) else data.m_FontData
                    if font_bytes and len(font_bytes) > 100:
                        ext = '.ttf' if font_bytes[:4] in [b'\x00\x01\x00\x00', b'OTTO'] else '.font'
                        return f"{asset_name}{ext}", font_bytes, f"Fonts/{asset_name}{ext}"
            except:
                pass
        
        # Shader
        elif obj_type == 'Shader':
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 100:
                    return f"{asset_name}.shader", raw[:500000], f"Shaders/{asset_name}.shader"
            except:
                pass
        
        # Mesh
        elif obj_type == 'Mesh':
            try:
                raw = obj.get_raw_data()
                if raw and len(raw) > 100:
                    return f"{asset_name}.mesh", raw[:500000], f"Meshes/{asset_name}.mesh"
            except:
                pass
        
        # MonoBehaviour
        elif obj_type == 'MonoBehaviour':
            try:
                info = {'type': obj_type, 'name': asset_name, 'path_id': obj.path_id}
                return f"{asset_name}.json", json.dumps(info, indent=2).encode('utf-8'), f"Scripts/{asset_name}.json"
            except:
                pass
    
    except Exception:
        pass
    
    return None

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_html(path):
    """Serve HTML interface."""
    return HTML_CONTENT

@app.route('/api/extract', methods=['POST', 'OPTIONS'])
def extract():
    """Main extraction endpoint."""
    if request.method == 'OPTIONS':
        return '', 200
    
    download_type = request.args.get('download_type', '')
    
    # Handle ZIP download
    if download_type == 'zip':
        if 'assets' not in ASSET_CACHE:
            return jsonify({'error': 'No assets in cache'}), 400
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for asset in ASSET_CACHE['assets']:
                zf.writestr(asset['path'], asset['data'])
        zip_buffer.seek(0)
        return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='extracted_assets.zip')
    
    # Handle single asset download
    if download_type == 'single':
        idx = request.args.get('file_index', type=int)
        if idx is None or 'assets' not in ASSET_CACHE or idx >= len(ASSET_CACHE['assets']):
            return jsonify({'error': 'Asset not found'}), 400
        asset = ASSET_CACHE['assets'][idx]
        return send_file(io.BytesIO(asset['data']), mimetype='application/octet-stream', as_attachment=True, download_name=asset['name'])
    
    # Handle extraction
    if 'asset_bundle' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['asset_bundle']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    
    try:
        # Read and decompress
        raw_data = file.read()
        if len(raw_data) > 10 * 1024 * 1024:
            return jsonify({'error': 'File exceeds 10MB limit'}), 400
        
        decompressed = decompress_data(raw_data)
        
        # Load with UnityPy
        try:
            import UnityPy
            env = UnityPy.load(decompressed)
        except Exception as e:
            return jsonify({'error': f'Invalid Unity bundle: {str(e)}'}), 400
        
        # Extract assets
        assets = []
        manifest = []
        seen_hashes = set()
        
        for obj in env.objects:
            result = extract_asset(obj, decompressed)
            if result:
                name, data, path = result
                content_hash = hashlib.md5(data).hexdigest()
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
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
        
        # Cleanup
        del env
        import gc
        gc.collect()
        
        # Store in cache
        ASSET_CACHE['assets'] = assets
        
        if not manifest:
            return jsonify({'error': 'No supported assets found in bundle'}), 400
        
        return jsonify({'files': manifest, 'count': len(manifest)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

# Vercel handler
app.debug = False