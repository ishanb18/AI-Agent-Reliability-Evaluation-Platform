import React, { useEffect, useState } from 'react';
import { Play, Download, Radio, Eye } from 'lucide-react';

export default function EvaluationsView({ setSelectedRunId, setActiveTab, selectedRunId }) {
  const [runs, setRuns] = useState([]);
  const [activeRunDetail, setActiveRunDetail] = useState(null);
  const [showRunModal, setShowRunModal] = useState(false);

  const [agents, setAgents] = useState([]);
  const [versions, setVersions] = useState([]);
  const [suites, setSuites] = useState([]);

  const [runForm, setRunForm] = useState({
    agentId: '',
    versionId: '',
    suiteId: '',
    judgeProvider: 'gemini',
  });

  useEffect(() => {
    fetchRuns();
    fetch('/agents').then(r => r.json()).then(setAgents).catch(() => []);
    fetch('/test-suites').then(r => r.json()).then(setSuites).catch(() => []);
  }, []);

  useEffect(() => {
    if (selectedRunId) {
      loadRunDetail(selectedRunId);
    }
  }, [selectedRunId]);

  async function fetchRuns() {
    try {
      const res = await fetch('/evaluations');
      const data = await res.json();
      setRuns(data);
      if (data.length > 0 && !selectedRunId) {
        loadRunDetail(data[0].id);
      }
    } catch (err) {
      console.error('Failed fetching runs:', err);
    }
  }

  async function loadRunDetail(runId) {
    setSelectedRunId(runId);
    try {
      const res = await fetch(`/evaluations/${runId}`);
      const data = await res.json();
      setActiveRunDetail(data);
    } catch (err) {
      console.error('Failed loading run detail:', err);
    }
  }

  async function handleAgentChange(agentId) {
    setRunForm(prev => ({ ...prev, agentId, versionId: '' }));
    try {
      const res = await fetch(`/agents/${agentId}/versions`);
      const data = await res.json();
      setVersions(data);
    } catch (err) {
      setVersions([]);
    }
  }

  async function handleLaunchRun(e) {
    e.preventDefault();
    try {
      const res = await fetch('/evaluations/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: parseInt(runForm.agentId),
          version_id: runForm.versionId ? parseInt(runForm.versionId) : null,
          suite_id: parseInt(runForm.suiteId),
          judge_provider: runForm.judgeProvider,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setShowRunModal(false);
        setSelectedRunId(data.run_id);
        setActiveTab('live'); // Switch to live SSE progress view!
      }
    } catch (err) {
      alert('Error launching run: ' + err.message);
    }
  }

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Evaluation Runs & Metrics</h1>
          <p>Inspect historical evaluation runs, metric scores, judge reasoning, and downloadable reports</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowRunModal(true)}>
          <Play size={16} /> Launch Evaluation Run
        </button>
      </div>

      {/* Runs Table */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">All Evaluation Runs</div>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Agent</th>
                <th>Version</th>
                <th>Status</th>
                <th>Passed / Total</th>
                <th>Avg Score</th>
                <th>Judge Model</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const score = r.avg_score !== null ? `${(r.avg_score * 100).toFixed(1)}%` : 'N/A';
                return (
                  <tr key={r.id}>
                    <td><strong>#{r.id}</strong></td>
                    <td>Agent #{r.agent_id}</td>
                    <td>{r.version_id ? `#${r.version_id}` : 'Base'}</td>
                    <td>
                      <span className={`badge ${r.status === 'completed' ? 'badge-pass' : 'badge-review'}`}>
                        {r.status}
                      </span>
                    </td>
                    <td>{r.passed_cases} / {r.total_cases}</td>
                    <td><strong>{score}</strong></td>
                    <td>{r.judge_provider || 'gemini'}</td>
                    <td>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
                        onClick={() => loadRunDetail(r.id)}
                      >
                        Inspect Drilldown ➔
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detailed Case Drilldown */}
      {activeRunDetail && (
        <div className="glass-card">
          <div className="card-header">
            <div>
              <div className="card-title">Run #{activeRunDetail.id} Case Breakdown</div>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Status: {activeRunDetail.status} | Passed: {activeRunDetail.passed_cases} / {activeRunDetail.total_cases}
              </span>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <a
                href={`/evaluations/${activeRunDetail.id}/export?format=markdown`}
                download
                className="btn btn-secondary"
                style={{ textDecoration: 'none' }}
              >
                <Download size={14} /> Download Report (MD)
              </a>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setSelectedRunId(activeRunDetail.id);
                  setActiveTab('live');
                }}
              >
                <Radio size={14} /> Watch Live SSE Stream
              </button>
            </div>
          </div>

          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Case #</th>
                  <th>Input Prompt</th>
                  <th>Latency</th>
                  <th>Status</th>
                  <th>Overall Score</th>
                  <th>Judge Reasoning & Metrics</th>
                </tr>
              </thead>
              <tbody>
                {(activeRunDetail.cases || []).map((c, idx) => {
                  const evalData = c.evaluation || {};
                  const score = c.status === 'success' && evalData.overall_score !== undefined
                    ? `${(evalData.overall_score * 100).toFixed(1)}%`
                    : '—';

                  const reasoningText = evalData.reasoning_dict
                    ? Object.entries(evalData.reasoning_dict).map(([k, v]) => `${k}: ${v}`).join(' | ')
                    : (c.error || 'Invocation success');

                  return (
                    <tr key={c.id || idx}>
                      <td>#{idx + 1}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', maxWidth: '240px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {c.input}
                      </td>
                      <td>{c.latency_ms ? `${Math.round(c.latency_ms)} ms` : '—'}</td>
                      <td>
                        <span className={`badge ${c.status === 'success' ? 'badge-pass' : 'badge-fail'}`}>
                          {c.status}
                        </span>
                      </td>
                      <td><strong>{score}</strong></td>
                      <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)', maxWidth: '350px' }}>
                        {reasoningText}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Launch Run Modal */}
      {showRunModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h2 style={{ marginBottom: '1rem' }}>Launch New Evaluation Run</h2>
            <form onSubmit={handleLaunchRun}>
              <div className="form-group">
                <label>Target Agent *</label>
                <select
                  className="form-input"
                  required
                  value={runForm.agentId}
                  onChange={(e) => handleAgentChange(e.target.value)}
                >
                  <option value="">Select an Agent...</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} (#${a.id})</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>Target Version (Optional)</label>
                <select
                  className="form-input"
                  value={runForm.versionId}
                  onChange={(e) => setRunForm({ ...runForm, versionId: e.target.value })}
                >
                  <option value="">Base Config (default)</option>
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>{v.version} ({v.model || 'inherited'})</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>Benchmark Test Suite *</label>
                <select
                  className="form-input"
                  required
                  value={runForm.suiteId}
                  onChange={(e) => setRunForm({ ...runForm, suiteId: e.target.value })}
                >
                  <option value="">Select a Suite...</option>
                  {suites.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.case_count || 0} cases)</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>LLM Judge Provider</label>
                <select
                  className="form-input"
                  value={runForm.judgeProvider}
                  onChange={(e) => setRunForm({ ...runForm, judgeProvider: e.target.value })}
                >
                  <option value="gemini">Gemini 1.5 Flash (default)</option>
                  <option value="groq">Groq (Llama 3)</option>
                </select>
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowRunModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Start Evaluation (Async SSE)</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
