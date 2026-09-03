let currentAgentId = null;
let currentAgent = null;

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

document.addEventListener('DOMContentLoaded', () => {
  loadAgents();

  // Modals trigger
  document.getElementById('btn-open-agent-modal').addEventListener('click', () => {
    document.getElementById('modal-agent').classList.add('active');
  });

  document.getElementById('btn-open-version-modal').addEventListener('click', () => {
    if (!currentAgentId) return alert('Please select an agent first');
    document.getElementById('modal-version').classList.add('active');
  });

  document.getElementById('btn-open-compare-modal').addEventListener('click', async () => {
    if (!currentAgentId) return alert('Please select an agent first');
    await populateCompareSelects();
    document.getElementById('modal-compare').classList.add('active');
  });

  // Submit forms
  document.getElementById('form-register-agent').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await API.post('/agents', {
        name: document.getElementById('reg-name').value,
        endpoint: document.getElementById('reg-endpoint').value,
        framework: document.getElementById('reg-framework').value || 'custom',
        model: document.getElementById('reg-model').value || 'gpt-4o',
        connection_type: 'rest_api'
      });
      closeModal('modal-agent');
      loadAgents();
    } catch (err) {
      alert('Failed to register agent: ' + err.message);
    }
  });

  document.getElementById('form-fork-version').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await API.post(`/agents/${currentAgentId}/versions`, {
        version: document.getElementById('ver-label').value,
        model: document.getElementById('ver-model').value || null,
        endpoint: document.getElementById('ver-endpoint').value || null,
        notes: document.getElementById('ver-notes').value || null
      });
      closeModal('modal-version');
      loadVersions(currentAgentId);
    } catch (err) {
      alert('Failed to fork version: ' + err.message);
    }
  });

  document.getElementById('form-quick-compare').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const res = await API.post(`/agents/${currentAgentId}/compare`, {
        baseline_version_id: parseInt(document.getElementById('comp-base-ver').value),
        candidate_version_id: parseInt(document.getElementById('comp-cand-ver').value),
        suite_id: parseInt(document.getElementById('comp-suite-id').value)
      });
      closeModal('modal-compare');
      window.location.href = `/ui/experiments?exp_id=${res.experiment_id}`;
    } catch (err) {
      alert('Version comparison failed: ' + err.message);
    }
  });
});

async function loadAgents() {
  const body = document.getElementById('agents-table-body');
  try {
    const agents = await API.get('/agents');
    if (!agents || agents.length === 0) {
      body.innerHTML = '<tr><td colspan="6" style="text-align:center;">No agents registered yet.</td></tr>';
      return;
    }

    body.innerHTML = agents.map(a => `
      <tr>
        <td>#${a.id}</td>
        <td><strong>${a.name}</strong></td>
        <td><span class="badge badge-info">${a.framework || 'custom'}</span></td>
        <td>${a.model || 'gpt-4o'}</td>
        <td style="font-family:var(--font-mono); font-size:0.8rem;">${a.endpoint || 'SDK'}</td>
        <td>
          <button class="btn btn-secondary" style="font-size:0.75rem; padding:0.3rem 0.6rem;" onclick="selectAgent(${a.id}, '${a.name}')">
            View Versions ➔
          </button>
        </td>
      </tr>
    `).join('');

    // Auto-select first agent
    if (agents.length > 0 && !currentAgentId) {
      selectAgent(agents[0].id, agents[0].name);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" style="color:var(--danger);">Error loading agents: ${err.message}</td></tr>`;
  }
}

async function selectAgent(id, name) {
  currentAgentId = id;
  document.getElementById('version-section').style.display = 'block';
  document.getElementById('selected-agent-title').textContent = `Lineage: ${name} (Agent #${id})`;
  loadVersions(id);
}

async function loadVersions(agentId) {
  const body = document.getElementById('versions-table-body');
  try {
    const versions = await API.get(`/agents/${agentId}/versions`);
    if (!versions || versions.length === 0) {
      body.innerHTML = '<tr><td colspan="7" style="text-align:center;">No custom versions forked yet. Defaulting to base config.</td></tr>';
      return;
    }

    body.innerHTML = versions.map(v => {
      const score = v.latest_eval_score !== null ? `${(v.latest_eval_score * 100).toFixed(1)}%` : 'Not run';
      return `
        <tr>
          <td>#${v.id}</td>
          <td><span class="badge badge-pass">${v.version}</span></td>
          <td>${v.model || 'inherited'}</td>
          <td><strong>${score}</strong></td>
          <td>${v.total_eval_runs}</td>
          <td style="color:var(--text-muted); font-size:0.85rem;">${v.notes || '—'}</td>
          <td style="color:var(--text-dim); font-size:0.8rem;">${new Date(v.created_at || Date.now()).toLocaleDateString()}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7" style="color:var(--danger);">Error loading versions: ${err.message}</td></tr>`;
  }
}

async function populateCompareSelects() {
  const versions = await API.get(`/agents/${currentAgentId}/versions`);
  const suites = await API.get('/test-suites');

  const baseSel = document.getElementById('comp-base-ver');
  const candSel = document.getElementById('comp-cand-ver');
  const suiteSel = document.getElementById('comp-suite-id');

  baseSel.innerHTML = versions.map(v => `<option value="${v.id}">${v.version} (${v.model || 'default'})</option>`).join('');
  candSel.innerHTML = versions.map(v => `<option value="${v.id}">${v.version} (${v.model || 'default'})</option>`).join('');
  if (versions.length > 1) candSel.selectedIndex = 1;

  suiteSel.innerHTML = suites.map(s => `<option value="${s.id}">${s.name} (${s.case_count || 0} cases)</option>`).join('');
}
