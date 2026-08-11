
document.addEventListener('DOMContentLoaded', function () {
    const observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (mutation) {
            mutation.addedNodes.forEach(function (node) {
                if (node.nodeType === 1 && node.classList && node.classList.contains('s3-upload-wrapper')) {
                    attachUploadListener(node);
                }
                if (node.nodeType === 1 && node.querySelectorAll) {
                    node.querySelectorAll('.s3-upload-wrapper').forEach(attachUploadListener);
                }
            });
        });
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true
    });

    document.querySelectorAll('.s3-upload-wrapper').forEach(attachUploadListener);
});

// Configuration
const CHUNK_SIZE = 50 * 1024 * 1024; // 50MB chunks

function getAppBasePath() {
    const parts = window.location.pathname.split('/');
    if (parts.length > 2 && (parts[1] === 'workspaces' || parts[1] === 'app') && parts[2]) {
        return `/workspaces/${parts[2]}`;
    }
    return '';
}

function generateUploadId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

function attachUploadListener(wrapper) {
    if (wrapper.dataset.listenerAttached) return;
    wrapper.dataset.listenerAttached = "true";

    const uploadZone = wrapper.querySelector('.s3-upload-zone');
    const placeholder = wrapper.querySelector('.upload-file-input-placeholder');

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.className = 'upload-file-input';
    fileInput.accept = wrapper.dataset.accept || 'image/*';
    placeholder.appendChild(fileInput);

    const fileInfo = wrapper.querySelector('.upload-file-info');
    const fileName = wrapper.querySelector('.file-name');
    const fileSize = wrapper.querySelector('.file-size');
    const progressContainer = wrapper.querySelector('.upload-progress-container');
    const progressBar = wrapper.querySelector('.upload-progress');
    const progressText = wrapper.querySelector('.upload-progress-text');
    const statusDiv = wrapper.querySelector('.upload-status');
    const hiddenInput = wrapper.querySelector('.upload-result');
    const cancelBtn = wrapper.querySelector('.upload-cancel-btn');

    if (!fileInput || !hiddenInput) return;

    let isAborted = false;

    uploadZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', function (e) {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
    });

    uploadZone.addEventListener('drop', function (e) {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', function (e) {
        const file = e.target.files[0];
        if (!file) return;
        handleFileSelect(file);
    });

    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            isAborted = true;
            statusDiv.innerText = "Aborting upload...";
            statusDiv.className = 'upload-status error';
        });
    }

    function resetUploadUI() {
        wrapper.classList.remove('has-file', 'uploading', 'processing-phase');
        progressContainer.style.display = 'none';
        fileInfo.style.display = 'none';
        progressBar.style.width = '0%';
        progressText.innerText = '0%';
        progressBar.style.backgroundColor = '';
        statusDiv.innerText = '';
        statusDiv.className = 'upload-status';
        fileName.innerText = '';
        fileSize.innerText = '';
        fileInput.value = '';
        if (cancelBtn) cancelBtn.style.display = 'none';
    }

    function formatTime(seconds) {
        if (!isFinite(seconds) || seconds < 0) return "-s";
        if (seconds < 60) return Math.ceil(seconds) + "s";
        const m = Math.floor(seconds / 60);
        const s = Math.ceil(seconds % 60);
        return `${m}m ${s}s`;
    }

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const info = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + info[i];
    }

    async function uploadChunk(uploadId, filename, chunk, partIndex, totalParts) {
        const action = partIndex === 0 ? 'overwrite' : 'append';
        const response = await fetch(`${getAppBasePath()}/upload_chunk`, {
            method: 'POST',
            headers: {
                'x-filename': filename,
                'x-upload-id': uploadId,
                'x-action': action
            },
            body: chunk
        });
        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.error || `Upload chunk failed: ${response.status}`);
        }
        return await response.json();
    }

    async function handleFileSelect(file) {
        if (!file) return;

        isAborted = false;
        wrapper.classList.add('has-file', 'uploading');
        fileName.innerText = file.name;
        fileSize.innerText = formatFileSize(file.size);
        progressContainer.style.display = 'block';
        if (cancelBtn) cancelBtn.style.display = 'inline-block';

        statusDiv.innerText = "Preparing upload...";
        statusDiv.className = 'upload-status processing';
        progressBar.style.width = "0%";
        progressBar.style.backgroundColor = '#4CAF50';
        progressText.innerText = "0%";

        const uploadId = generateUploadId();
        const totalParts = Math.ceil(file.size / CHUNK_SIZE);
        let lastResult = null;

        const progressSnapshots = [];
        const SPEED_WINDOW_MS = 5000;
        let bytesUploaded = 0;

        try {
            for (let i = 0; i < totalParts; i++) {
                if (isAborted) throw new Error("Aborted by user");

                const start = i * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, file.size);
                const chunk = file.slice(start, end);

                statusDiv.innerText = `Uploading part ${i + 1} of ${totalParts}...`;

                lastResult = await uploadChunk(uploadId, file.name, chunk, i, totalParts);

                bytesUploaded += (end - start);
                const percent = Math.round((bytesUploaded / file.size) * 100);
                progressBar.style.width = percent + '%';
                progressText.innerText = percent + '%';

                const now = Date.now();
                progressSnapshots.push({ time: now, bytes: bytesUploaded });
                while (progressSnapshots.length > 0 && (now - progressSnapshots[0].time) > SPEED_WINDOW_MS) {
                    progressSnapshots.shift();
                }
                if (progressSnapshots.length >= 2) {
                    const oldest = progressSnapshots[0];
                    const newest = progressSnapshots[progressSnapshots.length - 1];
                    const timeDiff = (newest.time - oldest.time) / 1000;
                    const bytesDiff = newest.bytes - oldest.bytes;
                    if (timeDiff > 0.5) {
                        const speed = bytesDiff / timeDiff;
                        const speedMBps = (speed / (1024 * 1024)).toFixed(2);
                        const remaining = file.size - bytesUploaded;
                        const eta = remaining / speed;
                        statusDiv.innerText = `Uploading... ${percent}% (${speedMBps} MB/s, ETA: ${formatTime(eta)})`;
                    }
                }
            }

            if (!lastResult || !lastResult.local_path) {
                throw new Error("Upload complete but server did not return local path.");
            }

            const localPath = lastResult.local_path;

            // Switch to Processing phase
            progressBar.style.width = "0%";
            progressBar.style.backgroundColor = '#2196F3';
            progressText.innerText = "0%";
            statusDiv.innerText = "Processing...";
            statusDiv.className = 'upload-status success';
            wrapper.classList.add('processing-phase');

            console.log("DEBUG: Upload complete. Local path:", localPath);
            setNativeValue(hiddenInput, localPath);

            pollStatus(uploadId);

        } catch (error) {
            console.error(error);
            statusDiv.innerText = `Error: ${error.message}`;
            statusDiv.className = 'upload-status error';
        }
    }

    function pollStatus(jobId) {
        const pollInterval = setInterval(() => {
            fetch(`${getAppBasePath()}/upload_status/${jobId}`)
                .then(r => r.json())
                .then(data => {
                    let p = data.progress || 0;
                    if (p > 100) p = 100;
                    statusDiv.innerText = data.message || "Processing...";
                    progressBar.style.width = p + '%';
                    progressText.innerText = p + '%';
                    if (p >= 100) {
                        clearInterval(pollInterval);
                        statusDiv.innerText = "Complete!";
                        progressBar.style.backgroundColor = '#4CAF50';
                        setTimeout(resetUploadUI, 2000);
                    }
                })
                .catch(console.error);
        }, 1000);
    }

    function setNativeValue(element, value) {
        const valueSetter = Object.getOwnPropertyDescriptor(element, 'value').set;
        const prototype = Object.getPrototypeOf(element);
        const prototypeValueSetter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
        if (valueSetter && valueSetter !== prototypeValueSetter) prototypeValueSetter.call(element, value);
        else valueSetter.call(element, value);
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
    }
}
