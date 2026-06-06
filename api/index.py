<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Assets Extractor</title>
    <meta name="description" content="Best Assets Extractor for AssetBundle, KTX, and Unity Assets. Made by MACBRUH_FF.">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;900&display=swap" rel="stylesheet">
    <style>
        @font-face { font-family: 'GFF-Bold'; src: url('https://dl.dir.freefiremobile.com/common/web_event/common/fonts/website/GFFLatinW05-Bold.woff'); }
        :root { --ff-yellow: #ffde00; --ff-cyan: #00f0ff; }
        body { background: #000; color: #fff; font-family: 'Inter', sans-serif; min-height: 100vh; overflow-x: hidden; background-image: url('https://dl.dir.freefiremobile.com/common/web_event/official2/dist/client/img/bg_pc.617c669.jpg'); background-size: cover; background-attachment: fixed; }
        body::before { content: ""; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.92); z-index: -1; }
        .gff { font-family: 'GFF-Bold', sans-serif; }
        
        /* Layout Requirement: Left Square, Right Rounded */
        .panel-container { 
            background: #0a0a0a; border: 1px solid #1f1f1f; 
            border-radius: 0 24px 24px 0; 
            position: relative; height: 420px; display: flex; flex-direction: column;
            box-shadow: 0 20px 50px rgba(0,0,0,0.5);
            transition: all 0.3s ease;
        }
        .panel-container::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--ff-yellow); }

        .custom-dropdown { position: relative; display: inline-block; }
        .dropdown-content { 
            display: none; position: absolute; right: 0; background: #121212; 
            min-width: 180px; border: 1px solid #2a2a2a; border-radius: 12px; 
            z-index: 50; padding: 8px; box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        }
        .dropdown-content.show { display: block; }
        
        .upload-area { border: 2px dashed #2a2a2a; border-radius: 16px; height: 120px; cursor: pointer; transition: 0.2s; }
        .upload-area:hover { border-color: var(--ff-yellow); background: #11110a; }
        
        .asset-row { background: #111; border: 1px solid #1f1f1f; border-radius: 12px; margin-bottom: 4px; padding: 10px; cursor: pointer; transition: 0.2s; }
        .asset-row:hover { border-color: #444; background: #161616; }
        .asset-row.active { border-color: var(--ff-yellow); background: #1a1a12; }

        .waveform { display: flex; align-items: flex-end; gap: 2px; height: 40px; justify-content: center; }
        .wave-bar { width: 3px; background: #333; border-radius: 2px; transition: 0.1s; }
        .playing .wave-bar { animation: waveAnim 0.5s ease-in-out infinite alternate; }
        @keyframes waveAnim { from { height: 5px; } to { height: 30px; } }

        .loader-overlay { position: absolute; inset: 0; background: rgba(0,0,0,0.7); display: none; align-items: center; justify-content: center; z-index: 40; border-radius: inherit; }
        
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-thumb { background: #222; border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--ff-yellow); }

        @media (max-width: 768px) { .panel-container { height: auto; min-height: 200px; border-radius: 16px; } }
    </style>
</head>
<body class="p-4 md:p-8">

    <!-- Header Section -->
    <div class="max-w-7xl mx-auto mb-10 text-center">
        <img src="https://dl.dir.freefiremobile.com/common/web_event/official2/dist/client/img/full_logo.969f536.png" class="h-8 mx-auto mb-4" alt="FF Logo">
        <h1 class="text-5xl md:text-7xl font-black italic tracking-tighter leading-none mb-1">
            ASSETS <span class="text-[#ffde00]">EXTRACTOR</span>
        </h1>
        <div class="flex items-center justify-center gap-3 text-zinc-400 text-xs font-bold tracking-widest uppercase">
            <i class="fa-solid fa-cube text-[10px] text-yellow-500"></i>
            <span>AssetBundle • KTX • Unity Assets Extractor</span>
            <i class="fa-solid fa-cube text-[10px] text-yellow-500"></i>
        </div>
    </div>

    <div class="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        <!-- Left: Upload -->
        <div class="panel-container">
            <div class="p-6 flex-1 flex flex-col">
                <div class="mb-4">
                    <h2 class="gff text-sm uppercase tracking-wider">Source Upload</h2>
                    <p class="text-[10px] text-zinc-500">Import your asset mapping file.</p>
                </div>
                
                <div class="upload-area flex flex-col items-center justify-center gap-2 group" id="dropZone" onclick="document.getElementById('fileInput').click()">
                    <i class="fa-solid fa-cloud-arrow-up text-2xl text-zinc-600 group-hover:text-yellow-500 transition-colors"></i>
                    <span id="fileName" class="text-[11px] font-bold text-zinc-400 uppercase gff">Select Unity File</span>
                    <input type="file" id="fileInput" class="hidden">
                </div>

                <button id="extractBtn" class="mt-4 bg-[#ffde00] text-black gff italic uppercase py-4 rounded-xl font-black text-sm shadow-[0_4px_0_#b39c00] active:translate-y-1 active:shadow-none transition-all">
                    Start Extraction
                </button>

                <div class="mt-auto pt-6 border-t border-zinc-900 flex items-center gap-3">
                    <div class="w-8 h-8 rounded-lg bg-zinc-900 border border-zinc-800 flex items-center justify-center">
                        <i class="fa-solid fa-terminal text-[10px] text-zinc-500"></i>
                    </div>
                    <div class="flex-1">
                        <p class="text-[9px] font-bold text-zinc-500 uppercase tracking-widest">Status Log</p>
                        <p id="statusText" class="text-[11px] text-zinc-400">Ready for processing...</p>
                    </div>
                </div>
            </div>
            <div id="uploadLoader" class="loader-overlay"><i class="fa-solid fa-circle-notch fa-spin text-yellow-500 text-2xl"></i></div>
        </div>

        <!-- Middle: List -->
        <div class="panel-container" id="listPanel">
            <div class="p-6 flex flex-col h-full">
                <div class="flex items-center justify-between mb-4">
                    <div>
                        <h2 class="gff text-sm uppercase tracking-wider">Asset List</h2>
                        <p id="countText" class="text-[10px] text-zinc-500">0 elements found</p>
                    </div>
                    
                    <div class="flex items-center gap-2">
                        <div class="custom-dropdown hidden" id="filterArea">
                            <button onclick="toggleDropdown('filterDropdown')" class="w-8 h-8 rounded-lg bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-400">
                                <i class="fa-solid fa-filter text-[10px]"></i>
                            </button>
                            <div id="filterDropdown" class="dropdown-content">
                                <div id="filterList" class="space-y-1"></div>
                            </div>
                        </div>

                        <div class="custom-dropdown hidden" id="zipArea">
                            <button onclick="toggleDropdown('zipDropdown')" class="w-8 h-8 rounded-lg bg-yellow-500/10 border border-yellow-500/20 flex items-center justify-center text-yellow-500">
                                <i class="fa-solid fa-file-zipper text-[10px]"></i>
                            </button>
                            <div id="zipDropdown" class="dropdown-content">
                                <button onclick="downloadZip('normal')" class="w-full text-left p-2 text-[10px] font-bold hover:bg-zinc-800 rounded">DOWNLOAD ALL</button>
                                <button onclick="downloadZip('grouped')" class="w-full text-left p-2 text-[10px] font-bold hover:bg-zinc-800 rounded">DOWNLOAD GROUPED</button>
                                <button id="filterDl" onclick="downloadZip('filtered')" class="w-full text-left p-2 text-[10px] font-bold hover:bg-zinc-800 rounded hidden">DOWNLOAD FILTERED</button>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="flex-1 overflow-y-auto pr-2" id="assetContainer">
                    <div class="h-full flex flex-col items-center justify-center opacity-20 text-center">
                        <i class="fa-solid fa-box-open text-3xl mb-2"></i>
                        <p class="text-[10px] uppercase font-bold gff">Awaiting Data</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Right: Preview -->
        <div class="panel-container">
            <div class="p-6 flex flex-col h-full">
                <div class="mb-4">
                    <h2 class="gff text-sm uppercase tracking-wider">Preview</h2>
                    <p class="text-[10px] text-zinc-500">Real-time content viewer.</p>
                </div>

                <div class="flex-1 bg-black/50 border border-zinc-900 rounded-2xl flex items-center justify-center overflow-hidden relative" id="previewDisplay">
                    <i class="fa-solid fa-eye text-2xl text-zinc-800"></i>
                </div>

                <div class="mt-4 flex items-center justify-between bg-zinc-900/50 p-3 rounded-xl border border-zinc-800">
                    <div class="truncate mr-4">
                        <p id="previewName" class="text-[11px] font-bold truncate">No selection</p>
                        <p id="previewType" class="text-[9px] text-zinc-500 uppercase tracking-widest font-black">Unknown</p>
                    </div>
                    <button id="singleDl" class="w-10 h-10 rounded-xl bg-zinc-800 flex items-center justify-center hover:bg-yellow-500 hover:text-black transition-all">
                        <i class="fa-solid fa-arrow-down-long"></i>
                    </button>
                </div>
            </div>
            <div id="previewLoader" class="loader-overlay"><i class="fa-solid fa-circle-notch fa-spin text-white text-2xl"></i></div>
        </div>

    </div>

    <script>
        let allAssets = [];
        let selectedTypes = new Set();

        const fileInput = document.getElementById('fileInput');
        const extractBtn = document.getElementById('extractBtn');

        function toggleDropdown(id) {
            document.getElementById(id).classList.toggle('show');
        }

        window.onclick = (e) => {
            if (!e.target.closest('.custom-dropdown')) {
                document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
            }
        };

        fileInput.onchange = (e) => {
            if(e.target.files[0]) document.getElementById('fileName').innerText = e.target.files[0].name;
        };

        extractBtn.onclick = async () => {
            if (!fileInput.files[0]) return;
            const formData = new FormData();
            formData.append('asset_bundle', fileInput.files[0]);

            document.getElementById('uploadLoader').style.display = 'flex';
            document.getElementById('statusText').innerText = "Processing engine data...";

            try {
                const res = await fetch('/api/extract', { method: 'POST', body: formData });
                const data = await res.json();
                allAssets = data.files || [];
                renderList();
                setupFilters();
                
                document.getElementById('filterArea').classList.remove('hidden');
                document.getElementById('zipArea').classList.remove('hidden');
                document.getElementById('countText').innerText = `${allAssets.length} elements loaded`;
                document.getElementById('statusText').innerText = "Extraction successful.";
                
                // Responsive Logic: Expand list if many files
                if(allAssets.length > 5) document.getElementById('listPanel').style.height = '420px';
                else document.getElementById('listPanel').style.height = 'auto';

            } catch (err) {
                document.getElementById('statusText').innerText = "Process error occurred.";
            } finally {
                document.getElementById('uploadLoader').style.display = 'none';
            }
        };

        function setupFilters() {
            const types = [...new Set(allAssets.map(a => a.label))].sort();
            const container = document.getElementById('filterList');
            container.innerHTML = '';
            types.forEach(type => {
                const div = document.createElement('div');
                div.className = "flex items-center gap-2 p-1 px-2 hover:bg-zinc-800 rounded cursor-pointer text-[10px] font-bold";
                div.innerHTML = `<input type="checkbox" value="${type}" class="accent-yellow-500"> <span>${type}</span>`;
                div.onclick = (e) => {
                    const cb = div.querySelector('input');
                    if(e.target !== cb) cb.checked = !cb.checked;
                    if(cb.checked) selectedTypes.add(type); else selectedTypes.delete(type);
                    renderList();
                };
                container.appendChild(div);
            });
        }

        function renderList() {
            const container = document.getElementById('assetContainer');
            container.innerHTML = '';
            const filtered = allAssets.filter(a => selectedTypes.size === 0 || selectedTypes.has(a.label));
            
            document.getElementById('filterDl').classList.toggle('hidden', selectedTypes.size === 0);

            filtered.forEach(asset => {
                const div = document.createElement('div');
                div.className = "asset-row flex items-center justify-between";
                div.onclick = () => showPreview(asset);
                div.innerHTML = `
                    <div class="flex items-center gap-3 truncate">
                        <div class="w-7 h-7 rounded bg-zinc-800 flex items-center justify-center flex-shrink-0">
                            <i class="fa-solid ${getIcon(asset.label)} text-[10px] text-zinc-500"></i>
                        </div>
                        <div class="truncate">
                            <p class="text-[11px] font-bold truncate text-zinc-300">${asset.name}</p>
                            <p class="text-[8px] text-zinc-600 font-black uppercase tracking-tighter">${asset.label}</p>
                        </div>
                    </div>
                    <i class="fa-solid fa-chevron-right text-[8px] text-zinc-800"></i>
                `;
                container.appendChild(div);
            });
        }

        function getIcon(label) {
            if(label.includes('Texture')) return 'fa-image';
            if(label.includes('Audio')) return 'fa-volume-high';
            if(label.includes('Mesh')) return 'fa-cube';
            if(label.includes('Video')) return 'fa-play';
            return 'fa-file-code';
        }

        async function showPreview(asset) {
            document.getElementById('previewLoader').style.display = 'flex';
            const display = document.getElementById('previewDisplay');
            const url = `/api/extract?download_type=single&file_index=${asset.index}`;
            
            document.getElementById('previewName').innerText = asset.name;
            document.getElementById('previewType').innerText = asset.label;
            document.getElementById('singleDl').onclick = () => window.location.href = url;

            const ext = asset.name.split('.').pop().toLowerCase();
            
            try {
                if(['png','jpg','webp'].includes(ext)) {
                    display.innerHTML = `<img src="${url}" class="max-w-full max-h-full object-contain">`;
                } else if(['mp3','wav','ogg'].includes(ext)) {
                    display.innerHTML = `
                        <div class="w-full p-6 text-center">
                            <div class="waveform" id="visualizer">${Array(20).fill('<div class="wave-bar"></div>').join('')}</div>
                            <audio id="player" src="${url}" class="hidden"></audio>
                            <button id="playBtn" class="mt-4 w-12 h-12 rounded-full bg-cyan-500 text-black flex items-center justify-center"><i class="fa-solid fa-play"></i></button>
                        </div>
                    `;
                    const p = document.getElementById('player');
                    const b = document.getElementById('playBtn');
                    b.onclick = () => {
                        if(p.paused) { p.play(); b.innerHTML = '<i class="fa-solid fa-pause"></i>'; document.getElementById('visualizer').classList.add('playing'); }
                        else { p.pause(); b.innerHTML = '<i class="fa-solid fa-play"></i>'; document.getElementById('visualizer').classList.remove('playing'); }
                    }
                } else if(ext === 'json' || ext === 'txt') {
                    const txt = await fetch(url).then(r => r.text());
                    display.innerHTML = `<pre class="text-[9px] p-4 text-zinc-500 w-full overflow-auto text-left h-full">${txt.slice(0,2000)}</pre>`;
                } else {
                    display.innerHTML = `<div class="text-center text-zinc-700"><i class="fa-solid fa-file-circle-question text-3xl mb-2"></i><p class="text-[10px] font-bold">NO PREVIEW AVAILABLE</p></div>`;
                }
            } catch(e) {
                display.innerHTML = "Preview Load Failed";
            } finally {
                document.getElementById('previewLoader').style.display = 'none';
            }
        }

        function downloadZip(mode) {
            const indices = allAssets.filter(a => selectedTypes.size === 0 || selectedTypes.has(a.label)).map(a => a.index).join(',');
            window.location.href = `/api/extract?download_type=zip&mode=${mode}&indices=${indices}`;
        }
    </script>
</body>
</html>