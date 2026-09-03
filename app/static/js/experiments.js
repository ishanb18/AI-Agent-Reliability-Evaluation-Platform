let currentExpId = null;

document.addEventListener('DOMContentLoaded', () => {
  loadExperiments();

  const urlParams = new URLSearchParams(window.location.search);
  const paramExpId = urlParams.get('exp_id');
  if (paramExpId) {
    selectExperiment(parseInt(paramExpId));
  }
});

async function loadExperiments() {
  const body = document.getElementById('experiments-table-body');
  try {
    const experiments = await API.get('/experiments');
    if (!experiments || experiments.length === 0) {
      body.innerHTML = '<tr><td colspan="8" style="text-align:center;">No version comparison experiments run yet. Trigger one in Agents Workspace!</td></tr>';
      return;
    }

    body.innerHTML = experiments.map(e => {
      const verdict = e.verdict || 'REVIEW';
      const badgeClass = verdict === 'PASS' ? 'badge-pass' : (verdict === 'FAIL' ? 'badge-fail' : 'badge-review');
      const deltaPercent = e.delta_avg_score !== null && e.delta_avg_score !== undefined
        ? `${e.delta_avg_score >= 0 ? '+' : ''}${(e.delta_avg_score * 100).toFixed(1)}%`
        : '—';

      return `
        <tr>
          <td>#${e.id}</td>
          <td>Agent #${e.agent_id}</td>
          <td>Run #${e.baseline_run_id}</td>
          <td>Run #${e.candidate_run_id}</td>
          <td><span class="badge ${badgeClass}">${verdict}</span></td>
          <td><strong style="color:${e.delta_avg_score >= 0 ? 'var(--success)' : 'var(--danger)'}">${deltaPercent}</strong></td>
          <td style="color:var(--text-dim); font-size:0.8rem;">${new Date(e.created_at || Date.now()).toLocaleDateString()}</td>
          <td>
            <button class="btn btn-secondary" style="font-size:0.75rem; padding:0.3rem 0.6rem;" onclick="selectExperiment(${e.id})">
              Compare Diffs ➔
            </button>
          </td>
        </tr>
      `;
    }).join('');

    if (experiments.length > 0 && !currentExpId) {
      selectExperiment(experiments[0].id);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="8" style="color:var(--danger);">Error loading experiments: ${err.message}</td></tr>`;
  }
}

async function selectExperiment(expId) {
  currentExpId = expId;
  document.getElementById('exp-detail-section').style.display = 'block';
  document.getElementById('selected-exp-title').textContent = `Experiment #${expId} Analysis`;

  const metricsBody = document.getElementById('exp-metrics-body');
  const verdictContainer = document.getElementById('verdict-badge-container');
  const reasonText = document.getElementById('exp-reason-text');

  try {
    const exp = await API.get(`/experiments/${expId}`);
    const verdict = exp.verdict || 'REVIEW';
    const badgeClass = verdict === 'PASS' ? 'badge-pass' : (verdict === 'FAIL' ? 'badge-fail' : 'badge-review');

    verdictContainer.innerHTML = `<span class="badge ${badgeClass}" style="font-size:1.1rem; padding:0.4rem 1rem;">VERDICT: ${verdict}</span>`;
    reasonText.textContent = exp.verdict_reason || 'Regression tolerance threshold checked against baseline score.';

    const diffs = exp.metric_diffs || {};
    const entries = Object.entries(diffs);

    if (entries.length === 0) {
      metricsBody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No metric breakdown available for this experiment.</td></tr>';
      return;
    }

    metricsBody.innerHTML = entries.map(([metric, data]) => {
      const base = data.baseline !== null ? `${(data.baseline * 100).toFixed(1)}%` : 'N/A';
      const cand = data.candidate !== null ? `${(data.candidate * 100).toFixed(1)}%` : 'N/A';
      const delta = data.delta !== null ? (data.delta * 100).toFixed(1) : 0;
      const deltaStr = data.delta !== null ? `${data.delta >= 0 ? '+' : ''}${delta}%` : '—';
      const isImproved = data.delta >= 0;

      return `
        <tr>
          <td><strong>${metric.toUpperCase()}</strong></td>
          <td>${base}</td>
          <td>${cand}</td>
          <td><strong style="color:${isImproved ? 'var(--success)' : 'var(--danger)'}">${deltaStr}</strong></td>
          <td>
            <span class="badge ${isImproved ? 'badge-pass' : 'badge-review'}">
              ${isImproved ? '▲ IMPROVED' : '▼ REGRESSED'}
            </span>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    metricsBody.innerHTML = `<tr><td colspan="5" style="color:var(--danger);">Error loading experiment details: ${err.message}</td></tr>`;
  }
}
