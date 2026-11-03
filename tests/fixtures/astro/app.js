/* Astro WebUI - Synthetic client application (issue #20) */

const API_BASE = '';

let currentState = {
    mount_status: 'unknown',
    camera_ready: false,
    tracking: false,
    rate_control: 'unavailable'
};

function showSection(name, button) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.getElementById(name).classList.add('active');
    if (button) button.classList.add('active');
    refreshState();
}

async function refreshState() {
    try {
        const res = await fetch(`${API_BASE}/api/astro/state`);
        const data = await res.json();
        currentState = data;
        updateStateUI(data);
    } catch (e) {
        console.error('Failed to refresh state:', e);
    }
}

function updateStateUI(state) {
    const mountEl = document.getElementById('mount-status');
    const readyEl = document.getElementById('camera-ready');
    const trackEl = document.getElementById('tracking-status');
    const rateEl = document.getElementById('rate-control');

    if (mountEl) mountEl.textContent = state.mount_status || 'unknown';
    if (readyEl) readyEl.textContent = state.camera_ready ? 'ready' : 'not ready';
    if (trackEl) trackEl.textContent = state.tracking ? 'tracking' : 'idle';
    if (rateEl) rateEl.textContent = state.rate_control || 'unavailable';

    // Color coding
    if (mountEl) {
        mountEl.className = 'value ' + (state.mount_status === 'idle' ? 'unknown' : '');
    }
    if (readyEl) {
        readyEl.className = 'value ' + (state.camera_ready ? '' : 'unknown');
    }
    if (rateEl) {
        rateEl.className = 'value ' + (state.rate_control === 'unavailable' ? 'unavailable' : '');
    }
}

async function setCameraReady(ready) {
    const resultEl = document.getElementById('camera-ready-result');
    try {
        const res = await fetch(`${API_BASE}/api/astro/camera-ready`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ready })
        });
        const data = await res.json();
        resultEl.className = 'result-area ' + (data.ok ? 'success' : 'error');
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.style.display = 'block';
        refreshState();
    } catch (e) {
        resultEl.className = 'result-area error';
        resultEl.textContent = 'Error: ' + e.message;
        resultEl.style.display = 'block';
    }
}

async function doCapture() {
    const exposure = document.getElementById('capture-exposure').value;
    const iso = document.getElementById('capture-iso').value;
    const resultEl = document.getElementById('capture-result');

    try {
        const res = await fetch(`${API_BASE}/api/astro/capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exposure_ms: parseInt(exposure), iso: parseInt(iso) })
        });
        const data = await res.json();
        resultEl.className = 'result-area ' + (data.ok ? 'success' : 'error');
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.style.display = 'block';
        refreshState();
    } catch (e) {
        resultEl.className = 'result-area error';
        resultEl.textContent = 'Error: ' + e.message;
        resultEl.style.display = 'block';
    }
}

async function doSolve() {
    const imageId = document.getElementById('solve-image').value;
    const resultEl = document.getElementById('solve-result');

    try {
        const res = await fetch(`${API_BASE}/api/astro/solve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_id: imageId })
        });
        const data = await res.json();
        resultEl.className = 'result-area ' + (data.ok ? 'success' : 'error');
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.style.display = 'block';
        refreshState();
    } catch (e) {
        resultEl.className = 'result-area error';
        resultEl.textContent = 'Error: ' + e.message;
        resultEl.style.display = 'block';
    }
}

async function doGoto() {
    const ra = document.getElementById('goto-ra').value;
    const dec = document.getElementById('goto-dec').value;
    const resultEl = document.getElementById('goto-result');

    try {
        const res = await fetch(`${API_BASE}/api/astro/goto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ra: parseFloat(ra), dec: parseFloat(dec) })
        });
        const data = await res.json();
        resultEl.className = 'result-area ' + (data.ok ? 'success' : 'error');
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.style.display = 'block';
        refreshState();
    } catch (e) {
        resultEl.className = 'result-area error';
        resultEl.textContent = 'Error: ' + e.message;
        resultEl.style.display = 'block';
    }
}

async function refreshDiagnostics() {
    const resultEl = document.getElementById('diagnostics-result');

    try {
        const res = await fetch(`${API_BASE}/api/astro/diagnostics`);
        const data = await res.json();
        resultEl.className = 'result-area success';
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.style.display = 'block';
    } catch (e) {
        resultEl.className = 'result-area error';
        resultEl.textContent = 'Error: ' + e.message;
        resultEl.style.display = 'block';
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    refreshState();
});
