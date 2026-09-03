document.addEventListener('DOMContentLoaded', async () => {
  try {
    const [agents, suites, runs, experiments, status] = await Promise.all([
      API.get('/agents'),
      API.get('/test-suites'),
      API.get('/evaluations'),
      API.get('/experiments'),
      API.get('/providers/status')
    ]);

    // Populate stat counters
    document.getElementById('stat-agents').textContent = agents.length || 0;
    document.getElementById('stat-suites').textContent = suites.length || 0;
    document.getElementById('stat-runs').textContent = runs.length || 0;
    document.getElementById('stat-experiments').textContent = experiments.length || 0;

    // Render Providers Table
    const providerBody = document.getElementById('provider-table-body');
    if (status && status.providers) {
      providerBody.innerHTML = status.providers.map(p => `
        <tr>
          <td><strong>${p.name.toUpperCase()}</strong></td>
          <td>
            <span class="badge ${p.available ? 'badge-pass' : 'badge-fail'}">
              ${p.available ? '● HEALTHY' : '○ UNAVAILABLE'}
            </span>
          </td>
          <td>${p.quota_estimate || 'Unlimited local'}</td>
          <td>${p.supports_fallback ? '✅ Automatic Failover' : 'Primary'}</td>
        </tr>
      `).join('');
    }

    // Render Recent Runs Table
    const recentBody = document.getElementById('recent-runs-body');
    if (runs && runs.length > 0) {
      recentBody.innerHTML = runs.slice(0, 5).map(r => {
        const scorePercent = r.avg_score !== null ? `${(r.avg_score * 100).toFixed(1)}%` : 'N/A';
        const statusBadge = r.status === 'completed' ? 'badge-pass' : (r.status === 'running' ? 'badge-info' : 'badge-review');
        return `
          <tr>
            <td><a href="/ui/evaluations?run_id=${r.id}" style="color: var(--primary); font-weight:600;">#${r.id}</a></td>
            <td>Agent #${r.agent_id}</td>
            <td><span class="badge ${statusBadge}">${r.status}</span></td>
            <td>${r.passed_cases} / ${r.total_cases}</td>
            <td><strong>${scorePercent}</strong></td>
            <td style="color: var(--text-dim);">${new Date(r.created_at || Date.now()).toLocaleDateString()}</td>
          </tr>
        `;
      }).join('');
    } else {
      recentBody.innerHTML = '<tr><td colspan="6" style="color: var(--text-muted); text-align:center;">No evaluation runs yet. Trigger one above!</td></tr>';
    }

  } catch (err) {
    console.error('Dashboard load error:', err);
  }
});
