document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  const runId = urlParams.get('run_id');

  if (!runId) {
    document.getElementById('stream-heading').textContent = 'Error: Missing run_id query parameter';
    return;
  }

  document.getElementById('stream-heading').textContent = `Live Evaluation Stream — Run #${runId}`;
  startSseStream(parseInt(runId));
});

let totalCasesCount = 0;
let completedCasesCount = 0;
let caseScores = [];

function startSseStream(runId) {
  const statusBadge = document.getElementById('sse-status-badge');
  const consoleLog = document.getElementById('event-log-console');
  const progressText = document.getElementById('progress-text');
  const progressFill = document.getElementById('live-progress-fill');
  const liveAvgScore = document.getElementById('live-avg-score');

  function appendLog(message, type = 'info') {
    const timeStr = new Date().toLocaleTimeString();
    const color = type === 'success' ? '#10b981' : (type === 'error' ? '#ef4444' : '#6366f1');
    const div = document.createElement('div');
    div.innerHTML = `<span style="color:var(--text-dim);">[${timeStr}]</span> <span style="color:${color}; font-weight:600;">${message}</span>`;
    consoleLog.appendChild(div);
    consoleLog.scrollTop = consoleLog.scrollHeight;
  }

  API.subscribeStream(
    runId,
    (event) => {
      statusBadge.textContent = '🟢 STREAM LIVE';
      statusBadge.className = 'badge badge-pass';

      if (event.event === 'started') {
        appendLog(`Evaluation run #${runId} started`, 'info');
      } else if (event.event === 'case_done') {
        completedCasesCount = event.case;
        totalCasesCount = event.total;
        const pct = totalCasesCount > 0 ? Math.round((completedCasesCount / totalCasesCount) * 100) : 0;

        progressFill.style.width = `${pct}%`;
        progressText.textContent = `Progress: ${completedCasesCount} / ${totalCasesCount} cases completed (${pct}%)`;

        if (event.score !== null && event.score !== undefined) {
          caseScores.push(event.score);
          const currentAvg = (caseScores.reduce((a, b) => a + b, 0) / caseScores.length) * 100;
          liveAvgScore.textContent = `Avg Score: ${currentAvg.toFixed(1)}%`;
        }

        const scoreStr = event.score !== null && event.score !== undefined ? `${(event.score * 100).toFixed(0)}%` : 'FAILED';
        appendLog(`Case ${event.case}/${event.total} finished — Score: ${scoreStr} (${event.latency_ms || 0}ms) — Input: "${event.input_preview || ''}"`, event.status === 'success' ? 'success' : 'error');
      } else if (event.event === 'run_complete' || event.event === 'completed') {
        statusBadge.textContent = '✅ RUN COMPLETE';
        statusBadge.className = 'badge badge-pass';
        progressFill.style.width = '100%';
        if (event.avg_score !== undefined) {
          liveAvgScore.textContent = `Final Avg Score: ${(event.avg_score * 100).toFixed(1)}%`;
        }
        appendLog(`🎉 Run #${runId} finished completely! Passed: ${event.passed || 0}, Failed: ${event.failed || 0}`, 'success');
      } else if (event.event === 'error' || event.event === 'failed') {
        statusBadge.textContent = '❌ RUN FAILED';
        statusBadge.className = 'badge badge-fail';
        appendLog(`Error: ${event.message || 'Run execution failed'}`, 'error');
      }
    },
    (err) => {
      statusBadge.textContent = '🔴 STREAM DISCONNECTED';
      statusBadge.className = 'badge badge-fail';
      appendLog(`Connection closed or stream completed.`, 'info');
    }
  );
}
