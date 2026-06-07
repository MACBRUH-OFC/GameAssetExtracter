<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Assets Extractor | ELITE</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;900&display=swap" rel="stylesheet">
<style>
@font-face { font-family: 'GFF-Bold'; src: url('https://dl.dir.freefiremobile.com/common/web_event/common/fonts/website/GFFLatinW05-Bold.woff') format('woff'); }
:root { --ff-yellow: #ffde00; --ff-emerald: #00ffa3; --ff-red: #ff3e3e; --ff-cyan: #38bdf8; }
body { background: #000; color: #fff; min-height: 100vh; background-image: url('https://dl.dir.freefiremobile.com/common/web_event/official2/dist/client/img/bg_pc.617c669.jpg'); background-size: cover; background-attachment: fixed; font-family: 'Inter', sans-serif; overflow-x: hidden; }
body::before { content: ""; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.94); z-index: -1; }
.gff { font-family: 'GFF-Bold', sans-serif; }

/* Main layout constraints: Left Square, Right Rounded */
.main-grid-container { display: grid; grid-template-columns: repeat(1, 1fr); gap: 24px; max-width: 1400px; margin: 0 auto; padding: 0 20px; }
@media (min-width: 1024px) { .main-grid-container { grid-template-columns: repeat(3, 1fr); } }

.uniform-panel { 
    background: #0a0a0a; border: 1px solid #1f1f1f; position: relative; 
    box-shadow: 0 25px 60px rgba(0, 0, 0, 0.8); height: 420px; display: flex; 
    flex-direction: column; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
/* Left square / Right rounded logic for the outer container */
@media (min-width: 1024px) {
    .panel-1 { border-radius: 0; }
    .panel-2 { border-radius: 0; }
    .panel-3 { border-radius: 0 24px 24px 0; }
}
@media (max-width: 1023px) {
    .uniform-panel { border-radius: 16px; margin-bottom: 16px; height: auto; min-height: 200px; }
    #listWindowPanel { height: 160px; overflow: hidden; }
    #listWindowPanel.expanded { height: 420px; }
}

.uniform-panel::before { content: ""; position: absolute; top: 0; left: 0; width: 4px; height: 100%; background: var(--ff-yellow); z-index: 10; }

.panel-body { padding: 24px; flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.scrollable-content-box { flex: 1; background: #060608; border: 1px solid #1a1a1f; border-radius: 16px; overflow: hidden; position: relative; }

/* Custom Buttons & Elements */
.btn-mechanical { cursor: pointer; font-family: 'GFF-Bold'; text-transform: uppercase; font-style: italic; display: flex; align-items: center; justify-content: center; border-radius: 16px; gap: 12px; background: var(--ff-yellow); color: #000; box-shadow: 0 5px 0 #c2a900; transition: all 0.1s; }
.btn-mechanical:active { transform: translateY(2px); box-shadow: 0 2px 0 #c2a900; }

.upload-deck { background: #121212; border: 2px dashed #2a2a2a; border-radius: 20px; display: flex; align-items: center; height: 110px; cursor: pointer; transition: all 0.2s; }
.upload-deck.dragover { border-color: var(--ff-yellow); background: #161612; }

/* Custom Dropdown Styling */
.custom-dropdown { position: relative; width: 100%; }
.dropdown-trigger { background: #121216; border: 1px solid #272732; padding: 8px 12px; border-radius: 10px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; font-size: 11px; font-weight: 700; color: #d4d4d8; }
.dropdown-menu { position: absolute; top: calc(100% + 8px); left: 0; right: 0; background: #0f0f13; border: 1px solid #272732; border-radius: 12px; z-index: 50; max-height: 200px; overflow-y: auto; display: none; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
.dropdown-menu.active { display: block; }
.dropdown-item { padding: 8px 12px; font-size: 11px; cursor: pointer; display: flex; align-items: center; gap: 8px; transition: background 0.2s; }
.dropdown-item:hover { background: #1a1a22; }
.dropdown-item.selected { color: var(--ff-yellow); }

/* Audio Visualizer */
#waveCanvas { width: 100%; height: 60px; }
.pixelated-render { image-rendering: pixelated; max-height: 100%; max-width: 100%; object-fit: contain; }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #222227; border-radius: 10px; }
</style>
</head>
<body>

<div class="flex flex-col items-center py-8">
    <img src="https://dl.dir.freefiremobile.com/common/web_event/official2/dist/client/img/full_logo.969f536.png" class="h-8 mb-4" alt="Free Fire Logo">
    <div class="text-center gff">
        <span class="text-[44px] md:text-[60px] font-black italic block leading-[0.8]">ASSETS</span>
        <span class="text-[44px] md:text-[60px] font-black italic block text-[#ffde00] leading-[0.9]">EXTRACTOR</span>
    </div>
    <div class="flex items-center gap-3 mt-2 opacity-80">
        <svg class="w-4 h-4 text-[#ffde00]" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path></svg>
        <span class="text-[10px] md:text-[12px] font-bold tracking-[0.2em] uppercase text-zinc-400">AssetBundle • KTX • Unity Assets Extractor</span>
        <svg class="w-4 h-4 text-[#ffde00]" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path></svg>
    </div>
</div>

<div class="main-grid-container">
    <!-- Column 1: Upload -->
    <div class="uniform-panel panel-1">
        <div class="panel-body justify-between">
            <div>
                <h2 class="text-xs font-black text-white uppercase tracking-wider mb-1 gff">Input Stream</h2>
                <p class="text-[11px] text-zinc-500">Initialize asset mapping sequence.</p>
            </div>
            <form id="extractorForm" class="flex-1 flex flex-col justify-center space-y-4 my-2">
                <div class="upload-deck" id="dropZone" onclick="document.getElementById('fileInput').click()">
                    <div class="w-16 h-full flex items-center justify-center border-r border-zinc-800">
                        <i id="uploadIcon" class="fa-solid fa-box-open text-xl text-zinc-600"></i>
                    </div>
                    <div class="flex-1 px-4 truncate">
                        <span id="fileStatusText" class="text-[9px] font-black text-zinc-500 block gff uppercase tracking-tighter">Standby</span>
                        <p id="fileLabel" class="gff text-[13px] text-zinc-300 truncate">Drop package here</p>
                    </div>
                </div>
                <input type="file" id="fileInput" class="hidden">
                <button type="submit" class="btn-mechanical w-full h-14 text-sm font-bold italic">
                    <i class="fa-solid fa-bolt"></i> Extract Now
                </button>
            </form>
            <div class="bg-[#121212] border border-zinc-800/50 rounded-xl p-3 flex items-center gap-3">
                <div id="statusIcon" class="text-zinc-600"><i class="fa-solid fa-terminal text-xs"></i></div>
                <div class="flex-1 min-w-0">
                    <span id="statusText" class="block text-[8px] font-black text-zinc-500 uppercase gff">System Log</span>
                    <p id="statusDescText" class="text-[10px] text-zinc-400 truncate">Awaiting file upload...</p>
                </div>
                <div id="statusBadge" class="text-[9px] gff px-2 py-0.5 bg-black rounded border border-zinc-800 text-zinc-500">IDLE</div>
            </div>
        </div>
    </div>

    <!-- Column 2: List -->
    <div id="listWindowPanel" class="uniform-panel panel-2">
        <div class="panel-body">
            <div class="flex items-center justify-between mb-4 border-b border-zinc-900 pb-3">
                <div class="min-w-0">
                    <h2 class="text-xs font-black text-white uppercase tracking-wider gff">Extracted</h2>
                    <p id="cacheCounterText" class="text-[10px] text-zinc-500 truncate">0 Items Found</p>
                </div>
                <div class="flex items-center gap-2 hidden" id="listActions">
                    <!-- Multi-Select Filter -->
                    <div class="custom-dropdown w-28" id="filterDropdown">
                        <div class="dropdown-trigger" id="filterTrigger">
                            <span>Filter</span> <i class="fa-solid fa-chevron-down text-[8px]"></i>
                        </div>
                        <div class="dropdown-menu custom-scrollbar" id="filterMenu"></div>
                    </div>
                    <!-- Zip Options -->
                    <div class="custom-dropdown w-10" id="zipDropdown">
                        <div class="dropdown-trigger justify-center" style="padding: 8px 0;">
                            <i class="fa-solid fa-file-zipper text-yellow-500"></i>
                        </div>
                        <div class="dropdown-menu right-0" id="zipMenu" style="width: 180px;">
                            <div class="dropdown-item" onclick="downloadZip('normal')"><i class="fa-solid fa-box"></i> Download All</div>
                            <div class="dropdown-item" onclick="downloadZip('filtered')"><i class="fa-solid fa-filter"></i> Filtered Only</div>
                            <div class="dropdown-item" onclick="downloadZip('grouped')"><i class="fa-solid fa-folder-tree"></i> Grouped Folders</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="scrollable-content-box">
                <div id="listEmptyState" class="absolute inset-0 flex flex-col items-center justify-center text-zinc-700">
                    <i class="fa-solid fa-layer-group text-2xl mb-2"></i>
                    <span class="text-[9px] font-bold gff uppercase tracking-widest">No assets parsed</span>
                </div>
                <div id="fileList" class="hidden absolute inset-0 overflow-y-auto p-2 space-y-1 custom-scrollbar"></div>
            </div>
        </div>
    </div>

    <!-- Column 3: Preview -->
    <div class="uniform-panel panel-3">
        <div class="panel-body">
            <div class="mb-4 border-b border-zinc-900 pb-3">
                <h2 class="text-xs font-black text-white uppercase tracking-wider gff">Live Preview</h2>
                <p class="text-[10px] text-zinc-500">Visualizing data stream.</p>
            </div>
            <div class="scrollable-content-box bg-black flex flex-col">
                <div id="previewFallback" class="flex-1 flex flex-col items-center justify-center text-zinc-800">
                    <i class="fa-solid fa-eye text-2xl mb-2"></i>
                    <span class="text-[9px] font-bold gff uppercase">Select Asset</span>
                </div>
                <div id="previewContent" class="hidden flex-1 flex flex-col p-2">
                    <div id="mediaBox" class="flex-1 bg-[#0d0d0f] rounded-xl border border-zinc-800 overflow-hidden flex items-center justify-center relative"></div>
                    <div class="mt-2 bg-[#121216] p-2 rounded-xl border border-zinc-800 flex items-center gap-3">
                        <div class="flex-1 min-w-0 px-1">
                            <p id="mediaTitle" class="text-[11px] font-bold text-zinc-300 truncate">Asset Name</p>
                            <p id="mediaTypeLabel" class="text-[8px] font-black text-zinc-600 uppercase gff">Type</p>
                        </div>
                        <a id="singleSaveBtn" href="#" class="w-8 h-8 rounded-lg bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-400 hover:text-white hover:bg-yellow-500 transition-all">
                            <i class="fa-solid fa-download text-xs"></i>
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
let activeCache = [];
let selectedFilters = new Set();
const fileInput = document.getElementById("fileInput");
const fileLabel = document.getElementById("fileLabel");
const fileList = document.getElementById("fileList");
const listWindowPanel = document.getElementById("listWindowPanel");

// UI Interactions
function setupDropdown(id) {
    const el = document.getElementById(id);
    const trigger = el.querySelector('.dropdown-trigger');
    const menu = el.querySelector('.dropdown-menu');
    trigger.onclick = (e) => {
        e.stopPropagation();
        document.querySelectorAll('.dropdown-menu').forEach(m => m !== menu && m.classList.remove('active'));
        menu.classList.toggle('active');
    };
}
setupDropdown('filterDropdown');
setupDropdown('zipDropdown');
document.onclick = () => document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.remove('active'));

// File Handling
fileInput.onchange = () => {
    const file = fileInput.files[0];
    if(!file) return;
    fileLabel.innerText = file.name;
    fileLabel.classList.add('text-[#ffde00]');
    document.getElementById('uploadIcon').className = "fa-solid fa-circle-check text-[#ffde00] text-xl";
    document.getElementById('fileStatusText').innerText = "READY FOR UPLOAD";
};

document.getElementById('extractorForm').onsubmit = async (e) => {
    e.preventDefault();
    const file = fileInput.files[0];
    if(!file) return;

    // Reset UI
    document.getElementById('statusBadge').innerText = "WORKING";
    document.getElementById('statusBadge').style.color = "var(--ff-yellow)";
    document.getElementById('listEmptyState').innerHTML = `<i class="fa-solid fa-circle-notch fa-spin text-yellow-500 text-xl"></i>`;
    
    const formData = new FormData();
    formData.append("asset_bundle", file);

    try {
        const res = await fetch("/api/extract", { method: "POST", body: formData });
        const data = await res.json();
        if(data.error) throw new Error(data.error);

        activeCache = data.files;
        renderList();
        updateFilterMenu();
        
        document.getElementById('listActions').classList.remove('hidden');
        document.getElementById('listEmptyState').classList.add('hidden');
        fileList.classList.remove('hidden');
        
        // Dynamic Resize
        if(activeCache.length < 5) {
            listWindowPanel.classList.remove('expanded');
        } else {
            listWindowPanel.classList.add('expanded');
        }

        document.getElementById('statusBadge').innerText = "SUCCESS";
        document.getElementById('statusBadge').style.color = "var(--ff-emerald)";
        document.getElementById('statusDescText').innerText = `Decoded ${activeCache.length} assets.`;
    } catch (err) {
        document.getElementById('statusBadge').innerText = "FAILED";
        document.getElementById('statusBadge').style.color = "var(--ff-red)";
        document.getElementById('statusDescText').innerText = err.message;
    }
};

function updateFilterMenu() {
    const labels = [...new Set(activeCache.map(i => i.label))].sort();
    const menu = document.getElementById('filterMenu');
    menu.innerHTML = `<div class="dropdown-item font-bold border-b border-zinc-800" onclick="toggleFilter('ALL')">Clear Filters</div>`;
    labels.forEach(label => {
        const item = document.createElement('div');
        item.className = `dropdown-item ${selectedFilters.has(label) ? 'selected' : ''}`;
        item.innerHTML = `<i class="fa-solid ${selectedFilters.has(label) ? 'fa-square-check' : 'fa-square'}"></i> ${label}`;
        item.onclick = (e) => { e.stopPropagation(); toggleFilter(label); };
        menu.appendChild(item);
    });
}

function toggleFilter(label) {
    if(label === 'ALL') selectedFilters.clear();
    else if(selectedFilters.has(label)) selectedFilters.delete(label);
    else selectedFilters.add(label);
    updateFilterMenu();
    renderList();
}

function getIcon(label) {
    const l = label.toLowerCase();
    if(l.includes('image') || l.includes('texture')) return 'fa-image text-yellow-500';
    if(l.includes('audio')) return 'fa-volume-high text-cyan-400';
    if(l.includes('mesh')) return 'fa-cube text-purple-400';
    if(l.includes('video')) return 'fa-play-circle text-red-500';
    return 'fa-file-code text-zinc-500';
}

function renderList() {
    fileList.innerHTML = "";
    const filtered = activeCache.filter(i => selectedFilters.size === 0 || selectedFilters.has(i.label));
    document.getElementById('cacheCounterText').innerText = `${filtered.length} Items Displayed`;
    
    filtered.forEach(item => {
        const row = document.createElement('div');
        row.className = "flex items-center justify-between p-2 rounded-xl bg-[#0f0f13] border border-zinc-900 hover:border-zinc-700 cursor-pointer group";
        row.onclick = () => selectFile(item);
        row.innerHTML = `
            <div class="flex items-center gap-3 min-w-0">
                <div class="w-8 h-8 rounded-lg bg-black flex items-center justify-center border border-zinc-800">
                    <i class="fa-solid ${getIcon(item.label)} text-[10px]"></i>
                </div>
                <div class="min-w-0">
                    <p class="text-[11px] font-bold text-zinc-300 truncate group-hover:text-white">${item.name}</p>
                    <p class="text-[8px] font-black text-zinc-600 uppercase gff">${item.label}</p>
                </div>
            </div>
        `;
        fileList.appendChild(row);
    });
}

async function selectFile(item) {
    document.getElementById('previewFallback').classList.add('hidden');
    document.getElementById('previewContent').classList.remove('hidden');
    const mediaBox = document.getElementById('mediaBox');
    const title = document.getElementById('mediaTitle');
    const typeLabel = document.getElementById('mediaTypeLabel');
    const saveBtn = document.getElementById('singleSaveBtn');
    
    title.innerText = item.name;
    typeLabel.innerText = item.label;
    const url = `/api/extract?download_type=single&file_index=${item.index}`;
    saveBtn.href = url;
    mediaBox.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin text-zinc-800"></i>`;

    const ext = item.name.split('.').pop().toLowerCase();
    
    if(['png','jpg','webp'].includes(ext)) {
        mediaBox.innerHTML = `<img src="${url}" class="pixelated-render p-4">`;
    } else if(['mp3','wav','ogg'].includes(ext)) {
        mediaBox.innerHTML = `
            <div class="flex flex-col items-center w-full p-4">
                <canvas id="waveCanvas"></canvas>
                <div class="flex items-center gap-4 mt-4 w-full">
                    <button id="playBtn" class="w-10 h-10 rounded-full bg-cyan-500 text-black flex items-center justify-center"><i class="fa-solid fa-play"></i></button>
                    <div class="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden"><div id="audioProg" class="h-full bg-cyan-500 w-0"></div></div>
                </div>
                <audio id="activeAudio" src="${url}" class="hidden"></audio>
            </div>`;
        initAudioPlayer();
    } else if(ext === 'mp4') {
        mediaBox.innerHTML = `<video src="${url}" controls class="max-h-full"></video>`;
    } else {
        try {
            const r = await fetch(url);
            const txt = await r.text();
            mediaBox.innerHTML = `<pre class="text-[10px] text-zinc-500 p-4 w-full h-full overflow-auto text-left whitespace-pre-wrap">${txt.slice(0, 5000)}</pre>`;
        } catch {
            mediaBox.innerHTML = `<i class="fa-solid fa-file-circle-exclamation text-zinc-800 text-3xl"></i>`;
        }
    }
}

function initAudioPlayer() {
    const audio = document.getElementById('activeAudio');
    const canvas = document.getElementById('waveCanvas');
    const ctx = canvas.getContext('2d');
    const playBtn = document.getElementById('playBtn');
    const prog = document.getElementById('audioProg');

    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaElementSource(audio);
    const analyser = audioCtx.createAnalyser();
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
    analyser.fftSize = 64;
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
        requestAnimationFrame(draw);
        analyser.getByteFrequencyData(dataArray);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const barWidth = (canvas.width / bufferLength) * 2.5;
        let x = 0;
        for(let i = 0; i < bufferLength; i++) {
            const barHeight = (dataArray[i] / 255) * canvas.height;
            ctx.fillStyle = audio.paused ? '#1a1a1a' : `rgb(56, 189, 248)`;
            ctx.fillRect(x, canvas.height - barHeight, barWidth, barHeight);
            x += barWidth + 2;
        }
        prog.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
    }
    draw();

    playBtn.onclick = () => {
        if(audioCtx.state === 'suspended') audioCtx.resume();
        if(audio.paused) { audio.play(); playBtn.innerHTML = '<i class="fa-solid fa-pause"></i>'; }
        else { audio.pause(); playBtn.innerHTML = '<i class="fa-solid fa-play"></i>'; }
    };
}

function downloadZip(mode) {
    let url = `/api/extract?download_type=zip&mode=${mode}`;
    if(mode === 'filtered') {
        const filteredIdx = activeCache
            .filter(i => selectedFilters.size === 0 || selectedFilters.has(i.label))
            .map(i => i.index);
        url += `&indices=${filteredIdx.join(',')}`;
    }
    window.location.href = url;
}
</script>
</body>
</html>