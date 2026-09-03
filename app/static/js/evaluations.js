let currentRunId = null;

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

document.addEventListener('DOMContentLoaded', async () => {
  loadRuns();

  const urlParams = new URLSearchParams(window.location.search);
  const paramRunId = urlParams.get('run_id');
  if (paramRunId) {
    selectRun(parseInt(paramRunId));
  }

  document.getElementById('btn-open-run-modal').addEventListener('click', async () => {
    await populateRunForm();
    document.getElementById('modal-run').classList.add('active');
  });

  document.getElementById('form-trigger-run').addEventListener('submit', async (e) => {
    e.preventDefault();
    const agentId = parseInt(document.getElementById('run-agent-id').value);
    const verId = document.getElementById('run-version-id').value;
    const suiteId = parseInt(document.getElementById('run-suite-id').value);
    const judgeProvider = document.getElementById('run-judge-provider').value;

    try {
      const res = await API.post('/evaluations/run', {
        agent_id: agentId,
        version_id: verId ? parseInt(verId) : null,
        suite_id: suiteId,
        judge_provider: judgeProvider
      });
      closeModal('modal-run');
      // Redirect to live SSE stream view!
      window.location.href = `/ui/live?run_id=${res.run_id}`;
    } catch (err) {
      alert('Failed to launch run: ' + err.message);
    }
  });

  document.getElementById('run-agent-id').addEventListener('change', async (e) => {
    const agentId = e.target.value;
    if (agentId) loadAgentVersionsDropdown(agentId);
  });
});

async function loadRuns() {
  const body = document.getElementById('runs-table-body');
  try {
    const runs = await API.get('/evaluations');
    if (!runs || runs.length === 0) {
      body.innerHTML = '<tr><td colspan="8" style="text-align:center;">No evaluation runs performed yet. Click "Launch New Evaluation Run" above!</td></tr>';
      return;
    }

    body.innerHTML = runs.map(r => {
      const scorePercent = r.avg_score !== null ? `${(r.avg_score * 100).toFixed(1)}%` : 'N/A';
      const statusBadge = r.status === 'completed' ? 'badge-pass' : (r.status === 'running' ? 'badge-info' : 'badge-review');
      return `
        <tr>
          <td>#${r.id}</td>
          <td>Agent #${r.agent_id}</td>
          <td>${r.version_id ? `#${r.version_id}` : 'Base'}</td>
          <td><span class="badge ${statusBadge}">${r.status}</span></td>
          <td>${r.passed_cases} / ${r.total_cases}</td>
          <td><strong>${scorePercent}</strong></td>
          <td>${r.judge_provider || 'gemini'}</td>
          <td>
            <button class="btn btn-secondary" style="font-size:0.75rem; padding:0.3rem 0.6rem;" onclick="selectRun(${r.id})">
              Inspect Cases ➔
            </button>
          </td>
        </tr>
      `;
    }).join('');

    if (runs.length > 0 && !currentRunId) {
      selectRun(runs[0].id);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="8" style="color:var(--danger);">Error loading runs: ${err.message}</td></tr>`;
  }
}

async function selectRun(runId) {
  currentRunId = runId;
  document.getElementById('run-detail-section').style.display = 'block';
  document.getElementById('selected-run-title').textContent = `Run #${runId} Case Drilldown`;
  document.getElementById('btn-export-report').href = `/evaluations/${runId}/export?format=markdown`;
  document.getElementById('btn-view-live').href = `/ui/live?run_id=${runId}`;

  const body = document.getElementById('cases-detail-body');
  try {
    const runData = await API.get(`/evaluations/${runId}`);
    const cases = runData.cases || [];

    if (cases.length === 0) {
      body.innerHTML = '<tr><td colspan="6" style="text-align:center;">No cases recorded for this run.</td></tr>';
      return;
    }

    body.innerHTML = cases.map((c, idx) => {
      const evalData = c.evaluation || {};
      const score = c.status === 'success' && evalData.overall_score !== undefined
        ? `${(evalData.overall_score * 100).toFixed(1)}%`
        : '—';

      const reasoningText = evalData.reasoning_dict
        ? Object.entries(evalData.reasoning_dict).map(([k, v]) => `<strong>${k}</strong>: ${v}`).join('<br>')
        : (c.error || 'Invocation success');

      return `
        <tr>
          <td>#${idx + 1}</td>
          <td style="font-family:var(--font-mono); font-size:0.85rem; max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
            ${escapeHtml(c.input || '')}
          </td>
          <td>${c.latency_ms ? `${c.latency_ms.toFixed(0)} ms` : '—'}</td>
          <td><span class="badge ${c.status === 'success' ? 'badge-pass' : 'badge-fail'}">${c.status}</span></td>
          <td><strong>${score}</strong></td>
          <td style="font-size:0.8rem; color:var(--text-muted); max-width:350px;">
            ${reasoningText}
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" style="color:var(--danger);">Error loading case detail: ${err.message}</td></tr>`;
  }
}

async function populateRunForm() {
  const agents = await API.get('/agents');
  const suites = await API.get('/test-suites');

  const agentSel = document.getElementById('run-agent-id');
  const suiteSel = document.getElementById('run-suite-id');

  agentSel.innerHTML = agents.map(a => `<option value="${a.id}">${a.name} (#${a.id})</option>`).join('');
  suiteSel.innerHTML = suites.map(s => `<option value="${s.id}">${s.name} (${s.case_count || 0} cases)</option>`).join('');

  if (agents.length > 0) {
    loadAgentVersionsDropdown(agents[0].id);
  }
}

async function loadAgentVersionsDropdown(agentId) {
  const verSel = document.getElementById('run-version-id');
  try {
    const versions = await API.get(`/agents/${agentId}/versions`);
    verSel.innerHTML = '<option value="">Base Config (default)</option>' +
      versions.map(v => `<option value="${v.id}">${v.version} (${v.model || 'inherited'})</option>`).join('');
  } catch (err) {
    verSel.innerHTML = '<option value="">Base Config (default)</option>';
  }
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
