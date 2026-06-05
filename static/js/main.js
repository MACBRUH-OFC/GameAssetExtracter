/*
 * UnityBundleExtractor - Frontend Logic
 * Author: lenzarchive (Redesigned by Antigravity AI)
 * License: MIT License
 * 
 * Optimized for Vercel serverless deployment using synchronous requests, AbortController, and modern state management.
 */

class UnityBundleExtractor {
    constructor() {
        this.currentSessionId = null;
        this.bundleMetadata = null;
        this.selectedAssets = new Set();
        this.allSelectedFiles = [];
        this.activeAbortController = null;
        this.isOperationInProgress = false;

        this.elements = {
            uploadForm: document.getElementById('upload-form'),
            fileInput: document.getElementById('file-input'),
            customFileUploadText: document.getElementById('custom-file-upload-text'),
            
            optionalUploadSection: document.getElementById('optional-upload-section'),
            optionalFileInput: document.getElementById('optional-file-input'),
            optionalFileUploadText: document.getElementById('optional-file-upload-text'),
            additionalFilesList: document.getElementById('additional-files-list'),

            uploadButton: document.getElementById('upload-button'),
            processingOverlay: document.getElementById('processing-overlay'),
            processingTitle: document.getElementById('processing-title'),
            processingSubtitle: document.getElementById('processing-subtitle'),
            progressBarInner: document.getElementById('progress-bar-inner'),
            cancelQueueButton: document.getElementById('cancel-queue-button'),

            resultsSection: document.getElementById('results-section'),
            metadataInfo: document.getElementById('metadata-info'),
            assetListContainer: document.getElementById('asset-list-container'),
            filterInput: document.getElementById('filter-input'),
            selectAllButton: document.getElementById('select-all-button'),
            deselectAllButton: document.getElementById('deselect-all-button'),
            extractButton: document.getElementById('extract-button'),
            
            statusMessage: document.getElementById('status-message'),
            errorInfoCard: document.getElementById('error-info-card'),
            errorInfoContent: document.getElementById('error-info-content'),

            sendLogCheckbox: document.getElementById('send-log-checkbox'),
            allowStorageCheckbox: document.getElementById('allow-storage-checkbox'),
            
            btcLink: document.getElementById('btc-link'),
            btcAddress: document.getElementById('btc-address'),
            assetClassesCard: document.getElementById('asset-classes-card'),
            assetClassesList: document.getElementById('asset-classes-list'),

            customConfirmModal: document.getElementById('custom-confirm-modal'),
            modalTitle: document.getElementById('modal-title'),
            modalMessage: document.getElementById('modal-message'),
            modalConfirmButton: document.getElementById('modal-confirm-button'),
            modalCancelButton: document.getElementById('modal-cancel-button'),
        };

        this.initializeEventListeners();
        this.initializeBTCAddressToggle();
    }

    initializeEventListeners() {
        // Stop browser from redirecting files dropped anywhere on page
        document.body.addEventListener('dragover', (e) => e.preventDefault());
        document.body.addEventListener('dragleave', (e) => e.preventDefault());
        document.body.addEventListener('drop', (e) => e.preventDefault());

        // Handle primary file selection
        this.elements.fileInput.addEventListener('change', (e) => {
            this.handlePrimaryFiles(e.target.files);
        });

        // Handle drag and drop for primary zone
        const primaryDropZone = document.getElementById('primary-drop-zone');
        if (primaryDropZone) {
            primaryDropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                primaryDropZone.classList.add('drag-over');
            });

            primaryDropZone.addEventListener('dragleave', (e) => {
                primaryDropZone.classList.remove('drag-over');
            });

            primaryDropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                primaryDropZone.classList.remove('drag-over');
                this.handlePrimaryFiles(e.dataTransfer.files);
            });
        }

        // Handle optional file selection
        this.elements.optionalFileInput.addEventListener('change', (e) => {
            this.handleOptionalFiles(e.target.files);
        });

        // Handle drag and drop for optional zone
        const optionalDropZone = document.getElementById('optional-drop-zone');
        if (optionalDropZone) {
            optionalDropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
                optionalDropZone.classList.add('drag-over');
            });

            optionalDropZone.addEventListener('dragleave', (e) => {
                optionalDropZone.classList.remove('drag-over');
            });

            optionalDropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                optionalDropZone.classList.remove('drag-over');
                this.handleOptionalFiles(e.dataTransfer.files);
            });
        }

        // Form submission (Analysis request)
        this.elements.uploadForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.runAnalysisWorkflow();
        });

        // Cancel button in processing overlay
        this.elements.cancelQueueButton.addEventListener('click', () => {
            this.requestCancellation();
        });

        // Asset selection & toolbar handlers
        this.elements.selectAllButton.addEventListener('click', () => this.selectAllAssets());
        this.elements.deselectAllButton.addEventListener('click', () => this.deselectAllAssets());
        this.elements.filterInput.addEventListener('input', () => this.filterAssetsList());
        this.elements.extractButton.addEventListener('click', () => this.runExtractionWorkflow());

        // Prevent Enter in search box from doing anything bad
        this.elements.filterInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
            }
        });

        // Click handler inside asset container for row-selection toggle
        this.elements.assetListContainer.addEventListener('click', (e) => {
            const listItem = e.target.closest('li');
            if (listItem) {
                const checkbox = listItem.querySelector('input[type="checkbox"]');
                if (checkbox && e.target.type !== 'checkbox') {
                    checkbox.checked = !checkbox.checked;
                    this.updateAssetSelectionState(parseInt(checkbox.dataset.assetIndex, 10), checkbox.checked);
                }
            }
        });

        // Change handler inside asset container for checkboxes (Asset level & Category level)
        this.elements.assetListContainer.addEventListener('change', (e) => {
            if (e.target.classList.contains('asset-checkbox')) {
                this.updateAssetSelectionState(parseInt(e.target.dataset.assetIndex, 10), e.target.checked);
            } else if (e.target.classList.contains('category-checkbox')) {
                this.updateCategorySelectionState(e.target.dataset.category, e.target.checked);
            }
        });

        // Event delegation to remove files from the preview list
        this.elements.additionalFilesList.addEventListener('click', (e) => {
            if (e.target.classList.contains('remove-file-btn')) {
                const fileIndex = parseInt(e.target.dataset.globalIndex, 10);
                if (!isNaN(fileIndex) && fileIndex >= 0 && fileIndex < this.allSelectedFiles.length) {
                    this.allSelectedFiles.splice(fileIndex, 1);
                    this.updateUploadFormUI();
                }
            }
        });
    }

    handlePrimaryFiles(files) {
        this.allSelectedFiles = []; // Clear current selection
        let mainFileFound = false;
        let validFiles = [];

        if (files.length > 0) {
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                if (this.isMainUnityFile(file.name)) {
                    if (!mainFileFound) {
                        this.allSelectedFiles.push(file);
                        mainFileFound = true;
                    } else {
                        validFiles.push(file);
                    }
                } else if (file.size > 0) {
                    validFiles.push(file);
                } else {
                    this.renderStatusMessage(`Skipped invalid or empty file: ${file.name}`, 'error', 4000);
                }
            }
        }

        this.allSelectedFiles.push(...validFiles);
        this.updateUploadFormUI();

        // Reveal the secondary drop zone if a valid main file exists
        this.elements.optionalUploadSection.style.display = mainFileFound ? 'block' : 'none';
        this.hideErrorBanner();
    }

    handleOptionalFiles(files) {
        let addedCount = 0;
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            if (file.size > 0) {
                // Prevent duplicate addition
                const isDuplicate = this.allSelectedFiles.some(f => f.name === file.name && f.size === file.size);
                if (!isDuplicate) {
                    this.allSelectedFiles.push(file);
                    addedCount++;
                }
            } else {
                this.renderStatusMessage(`Skipped empty file: ${file.name}`, 'error', 4000);
            }
        }

        this.updateUploadFormUI();
        this.hideErrorBanner();

        if (addedCount > 0) {
            this.renderStatusMessage(`Added ${addedCount} additional resource file(s).`, 'success', 3000);
        }
    }

    updateUploadFormUI() {
        const primaryFiles = this.allSelectedFiles.filter(file => this.isMainUnityFile(file.name));
        const primaryFile = primaryFiles.length > 0 ? primaryFiles[0] : null;
        const additionalFiles = this.allSelectedFiles.filter(file => file !== primaryFile);

        // Update main label text
        if (primaryFile) {
            this.elements.customFileUploadText.textContent = primaryFile.name;
        } else {
            this.elements.customFileUploadText.textContent = 'Drag & Drop or Choose Unity Main File (.bundle, .unity3d, .assets, .unitybundle)';
        }

        // Render additional files preview
        this.elements.additionalFilesList.innerHTML = '';
        if (additionalFiles.length > 0) {
            additionalFiles.forEach(file => {
                const globalIndex = this.allSelectedFiles.indexOf(file);
                const li = document.createElement('li');
                li.innerHTML = `
                    <span>📄 ${this.escapeHtml(file.name)} (${this.formatBytes(file.size)})</span>
                    <span class="remove-file-btn" data-global-index="${globalIndex}">✕</span>
                `;
                this.elements.additionalFilesList.appendChild(li);
            });
            this.elements.optionalFileUploadText.textContent = `Attached ${additionalFiles.length} additional resource file(s)`;
        } else {
            this.elements.optionalFileUploadText.textContent = 'Drag & Drop additional resource files here (optional)';
        }

        // Disable button if no main file is selected
        this.elements.uploadButton.disabled = (!primaryFile || this.isOperationInProgress);

        if (primaryFile && this.elements.statusMessage.style.display !== 'none') {
            this.hideStatusMessage();
        }
    }

    isMainUnityFile(filename) {
    return true;
}

    updateOverlayState(show, title = '', subtitle = '', percent = null) {
        this.isOperationInProgress = show;
        this.elements.uploadButton.disabled = show;
        this.elements.processingOverlay.style.display = show ? 'flex' : 'none';
        
        if (show) {
            this.elements.processingTitle.textContent = title;
            this.elements.processingSubtitle.textContent = subtitle;
            
            if (percent !== null) {
                this.elements.progressBarInner.style.width = `${percent}%`;
            } else {
                // Fake/pulsing bar width for general loaders
                this.elements.progressBarInner.style.width = '100%';
            }
        }
    }

    async runAnalysisWorkflow() {
        if (this.allSelectedFiles.length === 0) return;

        const primaryFile = this.allSelectedFiles.find(file => this.isMainUnityFile(file.name));
        if (!primaryFile) {
            this.renderStatusMessage("A main Unity file is required.", "error", 4000);
            return;
        }

        // Check overall size (Vercel payload constraint is 4.5MB, let's warn users for larger files)
        const totalSize = this.allSelectedFiles.reduce((sum, f) => sum + f.size, 0);
        const limitSize = 4.5 * 1024 * 1024; // 4.5MB
        if (totalSize > limitSize) {
            const sizeMB = (totalSize / (1024 * 1024)).toFixed(2);
            this.renderStatusMessage(`Warning: Total size is ${sizeMB}MB. Vercel Serverless limits payload bodies to 4.5MB. This upload might fail.`, "info", 6000);
        }

        this.updateOverlayState(true, 'Uploading & Analyzing...', 'Parsing Unity environment and indexing asset elements...');
        this.hideErrorBanner();
        this.elements.resultsSection.style.display = 'none';

        this.activeAbortController = new AbortController();
        const formData = new FormData();
        
        this.allSelectedFiles.forEach(file => {
            formData.append('files', file);
        });
        formData.append('send_log', this.elements.sendLogCheckbox.checked ? 'true' : 'false');
        formData.append('allow_storage', this.elements.allowStorageCheckbox.checked ? 'true' : 'false');

        try {
            const response = await fetch('/api/upload', {
                method: 'POST',
                body: formData,
                signal: this.activeAbortController.signal
            });

            if (!response.ok) {
                const errText = await response.text();
                throw new Error(errText || `Server returned error status ${response.status}`);
            }

            const data = await response.json();
            if (data.error) {
                throw new Error(data.error);
            }

            this.currentSessionId = data.session_id;
            this.bundleMetadata = data.metadata;
            
            this.updateOverlayState(false);
            this.renderAnalysisResults(data.metadata);
            this.renderStatusMessage('Bundle parsed and indexed successfully!', 'success', 3000);

        } catch (error) {
            if (error.name === 'AbortError') {
                this.renderStatusMessage('Operation cancelled.', 'info', 3000);
            } else {
                console.error(error);
                this.renderStatusMessage('Analysis failed.', 'error', 4000);
                this.displayErrorDetails(error.message);
            }
            this.updateOverlayState(false);
        } finally {
            this.activeAbortController = null;
        }
    }

    renderAnalysisResults(metadata) {
        if (!metadata || !metadata.bundle_info || !metadata.assets) {
            this.displayErrorDetails('Missing required asset metadata structure.');
            return;
        }

        // Render metadata grid
        const info = metadata.bundle_info;
        this.elements.metadataInfo.innerHTML = `
            <div><strong>File Name</strong><span>${this.escapeHtml(info.filename || 'Unknown')}</span></div>
            <div><strong>Size</strong><span>${this.formatBytes(info.size || 0)}</span></div>
            <div><strong>Unity Version</strong><span>${this.escapeHtml(info.unity_version || 'N/A')}</span></div>
            <div><strong>Platform</strong><span>${this.escapeHtml(info.platform || 'N/A')}</span></div>
            <div><strong>Objects Total</strong><span>${info.object_count || 0}</span></div>
            <div><strong>Compression</strong><span>${this.escapeHtml(info.compression || 'N/A')}</span></div>
        `;

        // Render classes sidebar list
        const classes = metadata.asset_classes || [];
        const classListHtml = classes.map(c => `<li>${this.escapeHtml(c)}</li>`).join('');
        this.elements.assetClassesList.innerHTML = `<ul class="class-list">${classListHtml}</ul>`;
        this.elements.assetClassesCard.style.display = classes.length > 0 ? 'block' : 'none';

        // Render assets inventory
        this.renderAssetsList(metadata.assets);
        
        // Show results segment, reset selections
        this.elements.resultsSection.style.display = 'block';
        this.selectedAssets.clear();
        this.syncExtractButtonUI();
    }

    renderAssetsList(assetsGrouped) {
        const container = this.elements.assetListContainer;
        container.innerHTML = '';

        // Sort categories alphabetically
        const sortedCategories = Object.keys(assetsGrouped).sort();

        if (sortedCategories.length === 0) {
            container.innerHTML = '<p style="padding: 20px; text-align: center; color: var(--text-muted);">No extractable assets found in this bundle.</p>';
            return;
        }

        sortedCategories.forEach(category => {
            const listItems = assetsGrouped[category];
            const details = document.createElement('details');
            details.className = 'asset-category';
            details.open = true;

            const summary = document.createElement('summary');
            summary.innerHTML = `
                <div class="asset-checkbox-wrapper">
                    <input type="checkbox" class="category-checkbox" data-category="${category}">
                </div>
                <span class="category-name">${this.escapeHtml(category)}</span>
                <span class="category-count">${listItems.length} items</span>
            `;

            const ul = document.createElement('ul');
            ul.className = 'asset-list';

            listItems.forEach(asset => {
                const li = document.createElement('li');
                li.innerHTML = `
                    <div class="asset-checkbox-wrapper">
                        <input type="checkbox" class="asset-checkbox" data-asset-index="${asset.index}" data-category="${category}">
                    </div>
                    <span class="asset-name">${this.escapeHtml(asset.name)}</span>
                    <span class="asset-type">${this.escapeHtml(asset.type)}</span>
                    <span class="asset-size">${this.formatBytes(asset.estimated_size)}</span>
                `;
                ul.appendChild(li);
            });

            details.appendChild(summary);
            details.appendChild(ul);
            container.appendChild(details);
        });
    }

    updateAssetSelectionState(index, checked) {
        if (checked) {
            this.selectedAssets.add(index);
        } else {
            this.selectedAssets.delete(index);
        }
        
        // Sync visual checkboxes
        const checkbox = this.elements.assetListContainer.querySelector(`.asset-checkbox[data-asset-index="${index}"]`);
        if (checkbox) {
            checkbox.checked = checked;
            
            // Check if all siblings in category are checked to sync the parent category checkbox
            const category = checkbox.dataset.category;
            this.syncCategoryParentCheckbox(category);
        }

        this.syncExtractButtonUI();
    }

    updateCategorySelectionState(category, checked) {
        const checkboxes = this.elements.assetListContainer.querySelectorAll(`.asset-checkbox[data-category="${category}"]`);
        checkboxes.forEach(cb => {
            cb.checked = checked;
            const idx = parseInt(cb.dataset.assetIndex, 10);
            if (checked) {
                this.selectedAssets.add(idx);
            } else {
                this.selectedAssets.delete(idx);
            }
        });

        this.syncCategoryParentCheckbox(category);
        this.syncExtractButtonUI();
    }

    syncCategoryParentCheckbox(category) {
        const parentCheckbox = this.elements.assetListContainer.querySelector(`.category-checkbox[data-category="${category}"]`);
        if (!parentCheckbox) return;

        const siblings = Array.from(this.elements.assetListContainer.querySelectorAll(`.asset-checkbox[data-category="${category}"]`));
        const checkedCount = siblings.filter(cb => cb.checked).length;

        if (checkedCount === 0) {
            parentCheckbox.checked = false;
            parentCheckbox.indeterminate = false;
        } else if (checkedCount === siblings.length) {
            parentCheckbox.checked = true;
            parentCheckbox.indeterminate = false;
        } else {
            parentCheckbox.checked = false;
            parentCheckbox.indeterminate = true;
        }
    }

    selectAllAssets() {
        const checkboxes = this.elements.assetListContainer.querySelectorAll('.asset-checkbox');
        checkboxes.forEach(cb => {
            cb.checked = true;
            this.selectedAssets.add(parseInt(cb.dataset.assetIndex, 10));
        });

        const categoryCheckboxes = this.elements.assetListContainer.querySelectorAll('.category-checkbox');
        categoryCheckboxes.forEach(cb => {
            cb.checked = true;
            cb.indeterminate = false;
        });

        this.syncExtractButtonUI();
    }

    deselectAllAssets() {
        const checkboxes = this.elements.assetListContainer.querySelectorAll('.asset-checkbox');
        checkboxes.forEach(cb => {
            cb.checked = false;
            this.selectedAssets.delete(parseInt(cb.dataset.assetIndex, 10));
        });

        const categoryCheckboxes = this.elements.assetListContainer.querySelectorAll('.category-checkbox');
        categoryCheckboxes.forEach(cb => {
            cb.checked = false;
            cb.indeterminate = false;
        });

        this.syncExtractButtonUI();
    }

    syncExtractButtonUI() {
        const size = this.selectedAssets.size;
        if (size > 0) {
            this.elements.extractButton.disabled = false;
            this.elements.extractButton.textContent = `Extract ${size} Selected as .ZIP`;
        } else {
            this.elements.extractButton.disabled = true;
            this.elements.extractButton.textContent = `Extract Selected as .ZIP`;
        }
    }

    filterAssetsList() {
        const query = this.elements.filterInput.value.toLowerCase().trim();
        const categories = this.elements.assetListContainer.querySelectorAll('.asset-category');

        categories.forEach(category => {
            const items = category.querySelectorAll('.asset-list li');
            let visibleCount = 0;

            items.forEach(item => {
                const name = item.querySelector('.asset-name').textContent.toLowerCase();
                const type = item.querySelector('.asset-type').textContent.toLowerCase();
                
                if (name.includes(query) || type.includes(query)) {
                    item.style.display = 'flex';
                    visibleCount++;
                } else {
                    item.style.display = 'none';
                }
            });

            // Hide the entire accordion if no assets in it match
            category.style.display = visibleCount > 0 ? 'block' : 'none';
        });
    }

    async runExtractionWorkflow() {
        if (this.selectedAssets.size === 0 || !this.currentSessionId) return;

        this.updateOverlayState(true, 'Extracting Assets...', 'Running type-specific decoders and generating your ZIP download...');
        this.hideErrorBanner();

        this.activeAbortController = new AbortController();
        const assetIndices = Array.from(this.selectedAssets);

        try {
            const response = await fetch('/api/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: this.currentSessionId,
                    selected_assets: assetIndices
                }),
                signal: this.activeAbortController.signal
            });

            if (!response.ok) {
                const errText = await response.text();
                throw new Error(errText || `Server returned error status ${response.status}`);
            }

            // Stream response directly as a blob (zip file)
            const blob = await response.blob();
            
            // Check if returned content is actually error JSON (if server crashed or sent JSON error back with 200 somehow)
            if (blob.type === 'application/json') {
                const text = await blob.text();
                const json = JSON.parse(text);
                throw new Error(json.error || 'Server returned an error.');
            }

            // Trigger browser download of zip blob
            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            
            // Derive download filename
            const filename = this.bundleMetadata.bundle_info.filename.split('.')[0] + '_extracted.zip';
            a.download = filename;
            
            document.body.appendChild(a);
            a.click();
            
            // Clean up resources
            window.URL.revokeObjectURL(downloadUrl);
            document.body.removeChild(a);

            this.updateOverlayState(false);
            this.renderStatusMessage('Assets extracted and downloaded successfully!', 'success', 4000);

        } catch (error) {
            if (error.name === 'AbortError') {
                this.renderStatusMessage('Operation cancelled.', 'info', 3000);
            } else {
                console.error(error);
                this.renderStatusMessage('Extraction failed.', 'error', 4000);
                this.displayErrorDetails(error.message);
            }
            this.updateOverlayState(false);
        } finally {
            this.activeAbortController = null;
        }
    }

    async requestCancellation() {
        if (!this.activeAbortController) return;

        const stopConfirmed = await this.triggerModalConfirm(
            'Stop Operation',
            'Are you sure you want to halt this process? If you stop, you will need to re-upload or select assets again.'
        );

        if (stopConfirmed) {
            this.activeAbortController.abort();
            this.updateOverlayState(false);
        }
    }

    triggerModalConfirm(title, message) {
        return new Promise(resolve => {
            this._modalResolve = resolve;
            this.elements.modalTitle.textContent = title;
            this.elements.modalMessage.textContent = message;
            this.elements.customConfirmModal.style.display = 'flex';
            
            // Temporary bound event listeners
            const onConfirm = () => {
                this._modalResolve(true);
                hide();
            };
            const onCancel = () => {
                this._modalResolve(false);
                hide();
            };

            const hide = () => {
                this.elements.customConfirmModal.style.display = 'none';
                this.elements.modalConfirmButton.removeEventListener('click', onConfirm);
                this.elements.modalCancelButton.removeEventListener('click', onCancel);
            };

            this.elements.modalConfirmButton.addEventListener('click', onConfirm);
            this.elements.modalCancelButton.addEventListener('click', onCancel);
        });
    }

    initializeBTCAddressToggle() {
        const address = "bc1q0ay7shy6zyy3xduf9hgsgu5crfzvpes93d48a6";
        const btcLink = this.elements.btcLink;
        const btcAddress = this.elements.btcAddress;

        if (!btcLink || !btcAddress) return;

        btcLink.addEventListener('click', (e) => {
            e.preventDefault();
            if (btcAddress.style.display === 'none') {
                btcAddress.textContent = address;
                btcAddress.style.display = 'block';
            } else {
                btcAddress.style.display = 'none';
            }
        });

        btcAddress.addEventListener('click', () => {
            navigator.clipboard.writeText(address).then(() => {
                const originalText = btcAddress.textContent;
                btcAddress.textContent = 'Copied Address to Clipboard!';
                setTimeout(() => {
                    btcAddress.textContent = originalText;
                }, 1500);
            }).catch(err => {
                console.error("Clipboard copy failed: ", err);
            });
        });
    }

    renderStatusMessage(message, type, duration = null) {
        this.elements.statusMessage.textContent = message;
        this.elements.statusMessage.className = `status-message ${type}`;
        this.elements.statusMessage.style.display = 'block';

        if (duration) {
            if (this.statusTimeout) clearTimeout(this.statusTimeout);
            this.statusTimeout = setTimeout(() => {
                this.hideStatusMessage();
            }, duration);
        }
    }

    hideStatusMessage() {
        this.elements.statusMessage.style.display = 'none';
        this.elements.statusMessage.textContent = '';
    }

    displayErrorDetails(errorString) {
        this.elements.errorInfoContent.textContent = errorString;
        this.elements.errorInfoCard.style.display = 'block';
        this.elements.errorInfoCard.scrollIntoView({ behavior: 'smooth' });
    }

    hideErrorBanner() {
        this.elements.errorInfoCard.style.display = 'none';
        this.elements.errorInfoContent.textContent = '';
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    escapeHtml(str) {
        if (typeof str !== 'string') return str;
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

// Instantiate extractor when document is fully parsed
// Fix upload button enable issue
document.addEventListener('change', function(e) {

    if (e.target.id === 'file-input') {

        const file = e.target.files[0];
        const uploadText = document.getElementById('custom-file-upload-text');
        const uploadButton = document.getElementById('upload-button');

        if (file) {
            uploadText.textContent =
                file.name +
                " (" +
                (file.size / 1024 / 1024).toFixed(2) +
                " MB)";
            uploadButton.disabled = false;
        }
    }

});
    window.extractorApp = new UnityBundleExtractor();
});
