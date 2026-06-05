/**
 * App Compiler — Frontend Logic
 * Handles pipeline interaction, progress display, JSON viewing, and code download.
 */

let currentResult = null;
let isGenerating = false;

// ============================================================
// DOM Elements
// ============================================================
const promptInput = document.getElementById('promptInput');
const generateBtn = document.getElementById('generateBtn');
const downloadBtn = document.getElementById('downloadBtn');
const progressSection = document.getElementById('progressSection');
const progressMessage = document.getElementById('progressMessage');
const outputSection = document.getElementById('outputSection');
const jsonOutput = document.getElementById('jsonOutput');
const errorSection = document.getElementById('errorSection');
const errorOutput = document.getElementById('errorOutput');
const clarificationSection = document.getElementById('clarificationSection');
const clarificationList = document.getElementById('clarificationList');
const costDisplay = document.getElementById('costDisplay');
const charCount = document.getElementById('charCount');
const apiKeyInput = document.getElementById('apiKeyInput');
const rememberKeyCheckbox = document.getElementById('rememberKey');

// ============================================================
// API Key persistence (sessionStorage default, optional localStorage)
// ============================================================
const API_KEY_STORAGE = 'app_compiler_deepseek_key';

// Restore saved key: prefer sessionStorage, fall back to localStorage
const savedKey = sessionStorage.getItem(API_KEY_STORAGE) || localStorage.getItem(API_KEY_STORAGE);
if (savedKey) {
    apiKeyInput.value = savedKey;
    // If we restored from localStorage, check the "remember" box
    if (localStorage.getItem(API_KEY_STORAGE)) {
        rememberKeyCheckbox.checked = true;
    }
}

// Save to sessionStorage on every change (cleared when browser closes)
// Also save to localStorage only if the user opted in via checkbox
apiKeyInput.addEventListener('input', () => {
    const key = apiKeyInput.value.trim();
    if (key) {
        sessionStorage.setItem(API_KEY_STORAGE, key);
        if (rememberKeyCheckbox.checked) {
            localStorage.setItem(API_KEY_STORAGE, key);
        }
    } else {
        sessionStorage.removeItem(API_KEY_STORAGE);
        localStorage.removeItem(API_KEY_STORAGE);
    }
});

// Sync localStorage when checkbox toggles
rememberKeyCheckbox.addEventListener('change', () => {
    const key = apiKeyInput.value.trim();
    if (rememberKeyCheckbox.checked && key) {
        localStorage.setItem(API_KEY_STORAGE, key);
    } else {
        localStorage.removeItem(API_KEY_STORAGE);
    }
});

// ============================================================
// Character counter
// ============================================================
promptInput.addEventListener('input', () => {
    charCount.textContent = `${promptInput.value.length}/3000`;
});

// ============================================================
// Example Chips
// ============================================================
document.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => {
        promptInput.value = chip.dataset.prompt;
        charCount.textContent = `${promptInput.value.length}/3000`;
    });
});

// ============================================================
// Generate
// ============================================================
generateBtn.addEventListener('click', async () => {
    if (isGenerating) return;

    const prompt = promptInput.value.trim();
    if (!prompt) return;

    // Set loading state
    isGenerating = true;
    generateBtn.textContent = '⏳ Generating...';
    generateBtn.disabled = true;
    downloadBtn.disabled = true;

    // Reset UI
    hideAllSections();
    progressSection.classList.remove('hidden');
    outputSection.classList.add('hidden');
    errorSection.classList.add('hidden');
    clarificationSection.classList.add('hidden');
    currentResult = null;

    // Reset pipeline stages
    document.querySelectorAll('.stage').forEach(s => {
        s.classList.remove('active', 'complete', 'error');
    });
    document.querySelectorAll('.stage-connector').forEach(c => c.classList.remove('done'));
    document.querySelectorAll('.stage-time').forEach(t => t.textContent = '');

    updatePipelineStage(1, 'active', 'Extracting intent...');

    // Validate API key is provided
    const userApiKey = apiKeyInput.value.trim();
    if (!userApiKey) {
        alert('Please enter your DeepSeek API key. Get one at https://platform.deepseek.com/');
        apiKeyInput.focus();
        isGenerating = false;
        generateBtn.textContent = '⚙️ Generate App Config';
        generateBtn.disabled = false;
        return;
    }

    try {
        const response = await fetch('/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt,
                api_key: userApiKey,
            }),
        });

        if (response.status === 429) {
            showError({ errors: [{ error: 'Rate limit exceeded. Please wait a minute before trying again.' }] });
            return;
        }

        const result = await response.json();

        if (result.needs_clarification) {
            showClarification(result);
            return;
        }

        if (!result.success) {
            showError(result);
            return;
        }

        currentResult = result;
        showResult(result);

    } catch (err) {
        showError({ errors: [{ error: err.message }] });
    } finally {
        isGenerating = false;
        generateBtn.textContent = '⚙️ Generate App Config';
        generateBtn.disabled = false;
    }
});

// ============================================================
// Pipeline Progress
// ============================================================
function updatePipelineStage(stageNum, status, message) {
    const stageEl = document.querySelector(`.stage[data-stage="${stageNum}"]`);

    // Mark previous stages complete
    for (let i = 1; i < stageNum; i++) {
        const prevStage = document.querySelector(`.stage[data-stage="${i}"]`);
        if (prevStage) {
            prevStage.classList.remove('active');
            prevStage.classList.add('complete');
        }
        const selector = `.stage:nth-of-type(${i}) ~ .stage-connector`;
        // Mark connectors: find the connector after stage i
        const connectors = document.querySelectorAll('.stage-connector');
        if (connectors[i - 1]) connectors[i - 1].classList.add('done');
    }

    if (stageEl) {
        stageEl.classList.remove('active', 'complete', 'error');
        stageEl.classList.add(status === 'error' ? 'error' : status === 'complete' ? 'complete' : 'active');
    }

    progressMessage.textContent = message;
}

// ============================================================
// Show Result
// ============================================================
function showResult(result) {
    outputSection.classList.remove('hidden');
    downloadBtn.disabled = false;

    // Update pipeline stages
    const timings = result.stage_timings || {};
    const stageNames = { stage1_intent: 1, stage2_design: 2, stage3_schema: 3, stage4_refinement: 4 };
    for (const [key, time] of Object.entries(timings)) {
        const stageNum = stageNames[key];
        if (stageNum) {
            updatePipelineStage(stageNum, 'complete', '');
            const timeEl = document.querySelector(`.stage[data-stage="${stageNum}"] .stage-time`);
            if (timeEl) timeEl.textContent = `${time}s`;
        }
    }

    progressMessage.textContent = result.validation_status === 'clean'
        ? '✓ Pipeline complete — config is valid'
        : `⚠ Pipeline complete — ${result.validation_status}`;

    // Update metrics
    document.getElementById('metricLatency').textContent = `${result.total_latency_seconds || 0}s`;
    document.getElementById('metricRepairs').textContent = result.repair_count || 0;
    document.getElementById('metricAssumptions').textContent = result.assumptions_count || 0;
    document.getElementById('metricCost').textContent = `$${(result.cost?.estimated_cost_usd || 0).toFixed(4)}`;
    document.getElementById('metricStatus').textContent = result.validation_status || 'unknown';

    // Quality score
    const qualityEl = document.getElementById('metricQuality');
    const qs = result.quality_score;
    if (qs && qs.composite !== undefined) {
        qualityEl.textContent = `${qs.composite}/100`;
        if (qs.composite >= 80) qualityEl.style.color = 'var(--green)';
        else if (qs.composite >= 50) qualityEl.style.color = 'var(--yellow)';
        else qualityEl.style.color = 'var(--red)';
    } else {
        qualityEl.textContent = '--';
        qualityEl.style.color = 'var(--accent)';
    }

    const statusEl = document.getElementById('metricStatus');
    if (result.validation_status === 'clean') statusEl.style.color = 'var(--green)';
    else if (result.validation_status === 'has_unresolved') statusEl.style.color = 'var(--red)';
    else statusEl.style.color = 'var(--yellow)';

    // Store config
    window._config = result.config || {};
    showTab('all');
    showGeneratedCode(result);
    showModifySection(result);

    // Update cost display
    if (result.cost) {
        costDisplay.textContent = `Cost: $${result.cost.estimated_cost_usd.toFixed(4)} | ${result.cost.total_tokens} tokens`;
    }

    // Trigger runtime test
    runRuntimeTest(result.config);
}

// ============================================================
// JSON Display
// ============================================================
function showTab(tabName) {
    const config = window._config || {};
    let data = tabName === 'all' ? config : (config[tabName] || {});
    jsonOutput.textContent = JSON.stringify(data, null, 2);

    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    const activeTab = document.querySelector(`.tab[data-tab="${tabName}"]`);
    if (activeTab) activeTab.classList.add('active');
}

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => showTab(tab.dataset.tab));
});

// ============================================================
// Show Generated Code (uses real code from server response)
// ============================================================
function showGeneratedCode(result) {
    const codeSection = document.getElementById('codeSection');
    const generatedCode = result.generated_code;
    const config = result.config;

    // Determine which files to show
    let files = {};

    if (generatedCode && Object.keys(generatedCode).length > 0) {
        // Use real generated code from the server
        files = generatedCode;
        // Sort: most important files first
        const priority = ['app.py', 'models.py', 'schemas.py', 'schema.sql', 'auth.py', 'business.py', 'requirements.txt', 'Dockerfile'];
        const sorted = {};
        priority.forEach(key => {
            if (files[key]) sorted[key] = files[key];
        });
        // Add any remaining files (templates, etc.)
        Object.keys(files).forEach(key => {
            if (!sorted[key]) sorted[key] = files[key];
        });
        files = sorted;
    } else {
        // Fallback: show config-based preview (only when no real code available)
        const tables = config?.db_schema?.tables || [];
        let sql = '-- Generated SQL Schema\n\n';
        tables.forEach(t => {
            const cols = (t.columns || []).map(c =>
                `  ${c.name} ${c.type}${c.primary_key ? ' PRIMARY KEY' : ''}${c.nullable === false ? ' NOT NULL' : ''}`
            ).join(',\n');
            sql += `CREATE TABLE ${t.name} (\n${cols}\n);\n\n`;
        });
        files['schema.sql (preview)'] = sql;

        const endpoints = config?.api_schema?.endpoints || [];
        let api = '# Generated FastAPI Routes (preview — download ZIP for real code)\n\n';
        api += 'from fastapi import FastAPI\n\napp = FastAPI()\n\n';
        endpoints.forEach(ep => {
            const method = (ep.method || 'get').toLowerCase();
            const path = ep.path || '/';
            const funcName = `${method}_${path.replace(/[\/{}]/g, '_').replace(/^_+/, '') || 'route'}`;
            api += `@app.${method}("${path}")\n`;
            api += `async def ${funcName}():\n`;
            api += `    """${ep.description || ''}"""\n    pass\n\n`;
        });
        files['app.py (preview)'] = api;
    }

    const codeTabs = document.getElementById('codeTabs');
    codeTabs.innerHTML = '';
    const fileNames = Object.keys(files);
    fileNames.forEach((name, i) => {
        const btn = document.createElement('button');
        btn.className = `code-tab${i === 0 ? ' active' : ''}`;
        btn.textContent = name;
        btn.addEventListener('click', () => {
            document.querySelectorAll('.code-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('codeOutput').textContent = files[name];
        });
        codeTabs.appendChild(btn);
    });
    document.getElementById('codeOutput').textContent = files[fileNames[0]] || '';
    codeSection.classList.remove('hidden');
}

// ============================================================
// Download Code (sends config, does NOT re-run pipeline)
// ============================================================
downloadBtn.addEventListener('click', async () => {
    if (!currentResult || !currentResult.config) return;

    downloadBtn.textContent = '⏳ Downloading...';
    downloadBtn.disabled = true;

    try {
        const response = await fetch('/download-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: currentResult.config }),
        });

        if (!response.ok) throw new Error('Download failed');

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'generated-app.zip';
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert('Download failed: ' + err.message);
    } finally {
        downloadBtn.textContent = '📦 Download Code';
        downloadBtn.disabled = false;
    }
});

// ============================================================
// Clarification
// ============================================================
function showClarification(result) {
    clarificationSection.classList.remove('hidden');
    progressSection.classList.add('hidden');

    const questions = result.clarification_questions || [];
    clarificationList.innerHTML = '';
    questions.forEach(q => {
        const li = document.createElement('li');
        li.textContent = q;
        clarificationList.appendChild(li);
    });

    document.getElementById('clarificationRetryBtn').onclick = async () => {
        const additional = document.getElementById('clarificationResponse').value.trim();
        const original = promptInput.value.trim();
        promptInput.value = original + '\n\nAdditional details: ' + additional;
        charCount.textContent = `${promptInput.value.length}/3000`;
        clarificationSection.classList.add('hidden');
        generateBtn.click();
    };
}

// ============================================================
// Error Display
// ============================================================
function showError(result) {
    errorSection.classList.remove('hidden');
    progressSection.classList.add('hidden');

    const errors = result.errors || [];
    errorOutput.textContent = errors.map(e =>
        `[Stage ${e.stage}] ${e.error}`
    ).join('\n\n') || 'Unknown error';
}

// ============================================================
// Copy & Download JSON
// ============================================================
document.getElementById('copyJsonBtn').addEventListener('click', () => {
    const text = jsonOutput.textContent;
    navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('copyJsonBtn');
        btn.textContent = '✓ Copied!';
        setTimeout(() => { btn.textContent = '📋 Copy JSON'; }, 2000);
    });
});

document.getElementById('downloadJsonBtn').addEventListener('click', () => {
    if (!currentResult || !currentResult.config) return;
    const blob = new Blob([JSON.stringify(currentResult.config, null, 2)], { type: 'application/json' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'generated-config.json';
    a.click();
    window.URL.revokeObjectURL(url);
});

// ============================================================
// Mid-way Modification
// ============================================================
const modifySection = document.getElementById('modifySection');
const modifyArchitecture = document.getElementById('modifyArchitecture');
const modifyStage = document.getElementById('modifyStage');
const modifyRunBtn = document.getElementById('modifyRunBtn');
const modifyStatus = document.getElementById('modifyStatus');

function showModifySection(result) {
    modifySection.classList.remove('hidden');
    // Pre-fill with Architecture IR for editing
    if (result.architecture_ir) {
        modifyArchitecture.value = JSON.stringify(result.architecture_ir, null, 2);
    }
    // Store full state for modify
    window._lastResult = result;
}

modifyRunBtn.addEventListener('click', async () => {
    if (isGenerating) return;
    const stage = parseInt(modifyStage.value);
    let editedArch = null;
    let editedIntent = null;

    try {
        if (stage <= 2) {
            editedArch = JSON.parse(modifyArchitecture.value);
        }
    } catch (e) {
        modifyStatus.textContent = 'Invalid JSON in editor';
        modifyStatus.style.color = 'var(--red)';
        return;
    }

    const lastResult = window._lastResult || {};
    const userApiKey = apiKeyInput.value.trim();
    if (!userApiKey) {
        alert('Please enter your DeepSeek API key.');
        apiKeyInput.focus();
        return;
    }

    isGenerating = true;
    modifyRunBtn.disabled = true;
    modifyRunBtn.textContent = '⏳ Re-running...';
    modifyStatus.textContent = '';
    modifyStatus.style.color = 'var(--green)';

    try {
        const response = await fetch('/modify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_prompt: promptInput.value.trim(),
                api_key: userApiKey,
                stage: stage,
                intent_ir: stage <= 1 ? lastResult.intent_ir : editedIntent,
                architecture_ir: stage <= 2 ? editedArch : lastResult.architecture_ir,
            }),
        });

        const result = await response.json();

        if (result.success) {
            modifyStatus.textContent = `Done in ${result.total_latency_seconds}s`;
            currentResult = result;
            showResult(result);
            showModifySection(result);
        } else {
            modifyStatus.textContent = 'Failed: ' + (result.errors?.[0]?.error || 'unknown');
            modifyStatus.style.color = 'var(--red)';
        }
    } catch (err) {
        modifyStatus.textContent = 'Error: ' + err.message;
        modifyStatus.style.color = 'var(--red)';
    } finally {
        isGenerating = false;
        modifyRunBtn.disabled = false;
        modifyRunBtn.textContent = '🔄 Re-run from Selected Stage';
    }
});

// ============================================================
// Runtime Test
// ============================================================
async function runRuntimeTest(config) {
    const runtimeSection = document.getElementById('runtimeSection');
    const runtimeStatus = document.getElementById('runtimeStatus');
    const runtimeSpinner = document.getElementById('runtimeSpinner');
    const runtimeResult = document.getElementById('runtimeResult');
    const smokeContainer = document.getElementById('smokeTestsContainer');
    const smokeBody = document.getElementById('smokeTestsBody');
    const runtimeErrors = document.getElementById('runtimeErrors');

    runtimeSection.classList.remove('hidden');
    runtimeSpinner.classList.remove('hidden');
    runtimeResult.textContent = 'Starting runtime test...';
    smokeContainer.classList.add('hidden');
    runtimeErrors.classList.add('hidden');

    if (!config || !config.metadata) {
        runtimeSpinner.classList.add('hidden');
        runtimeResult.innerHTML = '<span style=\"color:var(--yellow)\">No config to test — generate first</span>';
        return;
    }
    try {
        const reqBody = JSON.stringify({ config: config, keep_alive: 300 });
        const response = await fetch('/run-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: reqBody,
        });

        const rawText = await response.text();
        let data = {};
        try { data = JSON.parse(rawText); } catch(e) { data = {_parse_error: e.message, _raw: rawText.slice(0,300)}; }
        runtimeSpinner.classList.add('hidden');

        if (data.base_url && data.startup_latency_seconds > 0 && data.success !== false) {
            const proxyUrl = `/sandbox/${data.port}/`;
            const passed = data.smoke_tests_passed || 0;
            const failed = data.smoke_tests_failed || 0;
            const note = failed > 0 ? ' (auth/admin endpoints expected)' : '';
            const startTime = Date.now();
            const timeoutMs = 300 * 1000;

            function updateTimer() {
                const elapsed = Math.floor((Date.now() - startTime) / 1000);
                const remaining = Math.max(0, Math.floor(timeoutMs / 1000) - elapsed);
                const mins = Math.floor(remaining / 60);
                const secs = remaining % 60;
                const timerEl = document.getElementById('sandboxTimer');
                if (timerEl && remaining > 0) {
                    timerEl.textContent = `Time left: ${mins}m ${secs}s`;
                    setTimeout(updateTimer, 1000);
                } else if (timerEl) {
                    timerEl.textContent = 'App has stopped. Re-run.';
                    timerEl.style.color = 'var(--red)';
                }
            }

            runtimeResult.innerHTML = `
                <span style="color:var(--green);font-weight:600;">
                    App is LIVE at <a href="${proxyUrl}" target="_blank" style="color:var(--accent);text-decoration:underline;">${proxyUrl}</a>
                </span>
                <span style="color:var(--text2);font-size:0.8rem;display:block;margin-top:4px;">
                    (proxied through main server — same port, no firewall issues)
                </span>
                <span id="sandboxTimer" style="color:var(--text2);font-size:0.8rem;display:block;margin-top:2px;"></span>
                <span style="color:var(--text2);font-size:0.8rem;display:block;">
                    Smoke: ${passed}/${passed+failed} passed${note} · started ${data.startup_latency_seconds || 0}s ago
                </span>`;
            setTimeout(updateTimer, 1000);
            setTimeout(updateTimer, 1000);
            setTimeout(() => {
                const btn = document.getElementById('testConnBtn');
                if (btn) btn.addEventListener('click', testConnection);
            }, 100);
        } else {
            runtimeResult.innerHTML = `<span style="color:var(--red)">Runtime test failed</span>`;
            runtimeErrors.classList.remove('hidden');
            runtimeErrors.textContent = 'Response: ' + (data._raw || rawText || JSON.stringify(data));
        }

        // Display smoke test results
        if (data.smoke_tests && data.smoke_tests.length > 0) {
            smokeContainer.classList.remove('hidden');
            smokeBody.innerHTML = '';
            data.smoke_tests.forEach(t => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td><code>${t.method}</code></td>
                    <td><code>${t.endpoint}</code></td>
                    <td>${t.expected_status}</td>
                    <td>${t.actual_status || 'ERR'}</td>
                    <td>${t.latency_ms}ms</td>
                    <td>${t.passed
                        ? '<span style="color:var(--green)">Pass</span>'
                        : `<span style="color:var(--red)" title="${t.error || ''}">Fail</span>`
                    }</td>
                `;
                smokeBody.appendChild(row);
            });
        }

    } catch (err) {
        runtimeSpinner.classList.add('hidden');
        runtimeResult.innerHTML = `<span style="color:var(--red)">Runtime test error: ${err.message}</span>`;
    }
}

// ============================================================
// Keyboard Shortcut (Ctrl+Enter to generate)
// ============================================================
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        generateBtn.click();
    }
});

// ============================================================
// Helpers
// ============================================================
function hideAllSections() {
    [outputSection, errorSection, clarificationSection].forEach(s => s.classList.add('hidden'));
    const runtimeSection = document.getElementById('runtimeSection');
    if (runtimeSection) runtimeSection.classList.add('hidden');
}
