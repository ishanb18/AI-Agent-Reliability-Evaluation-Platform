import React, { useEffect, useState } from 'react';
import { FlaskConical, ArrowUpRight, CheckCircle2, AlertOctagon, HelpCircle } from 'lucide-react';

export default function ExperimentsView({ selectedExpId, setSelectedExpId, setActiveTab }) {
  const [experiments, setExperiments] = useState([]);
  const [activeExpDetail, setActiveExpDetail] = useState(null);

  useEffect(() => {
    fetchExperiments();
  }, []);

  useEffect(() => {
    if (selectedExpId) {
      loadExpDetail(selectedExpId);
    }
  }, [selectedExpId]);

  async function fetchExperiments() {
    try {
      const res = await fetch('/experiments');
      const data = await res.json();
      setExperiments(data);
      if (data.length > 0 && !selectedExpId) {
        loadExpDetail(data[0].id);
      }
    } catch (err) {
      console.error('Failed fetching experiments:', err);
    }
  }

  async function loadExpDetail(expId) {
    setSelectedExpId(expId);
    try {
      const res = await fetch(`/experiments/${expId}`);
      const data = await res.json();
      setActiveExpDetail(data);
    } catch (err) {
      console.error('Failed loading experiment detail:', err);
    }
  }

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Experiment & Version Comparison Visualizer</h1>
          <p>Side-by-side V1 vs V2 regression analysis, metric diffs, and PASS/REVIEW/FAIL deployment gates</p>
        </div>
        <button className="btn btn-primary" onClick={() => setActiveTab('agents')}>
          + Run New Comparison
        </button>
      </div>

      {/* Experiments Table */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">All Version Experiments</div>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>Exp ID</th>
                <th>Agent ID</th>
                <th>Baseline (V1)</th>
                <th>Candidate (V2)</th>
                <th>Verdict</th>
                <th>Avg Delta</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {experiments.map((e) => {
                const verdict = e.verdict || 'REVIEW';
                const badgeClass = verdict === 'PASS' ? 'badge-pass' : (verdict === 'FAIL' ? 'badge-fail' : 'badge-review');
                const deltaPercent = e.delta_avg_score !== null && e.delta_avg_score !== undefined
                  ? `${e.delta_avg_score >= 0 ? '+' : ''}${(e.delta_avg_score * 100).toFixed(1)}%`
                  : '—';

                return (
                  <tr key={e.id}>
                    <td><strong>#{e.id}</strong></td>
                    <td>Agent #{e.agent_id}</td>
                    <td>Run #{e.baseline_run_id}</td>
                    <td>Run #{e.candidate_run_id}</td>
                    <td><span className={`badge ${badgeClass}`}>{verdict}</span></td>
                    <td><strong style={{ color: e.delta_avg_score >= 0 ? 'var(--success)' : 'var(--danger)' }}>{deltaPercent}</strong></td>
                    <td>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
                        onClick={() => loadExpDetail(e.id)}
                      >
                        Compare Metric Diffs ➔
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Metric Diffs Detail */}
      {activeExpDetail && (
        <div className="glass-card">
          <div className="card-header">
            <div>
              <div className="card-title">Experiment #{activeExpDetail.id} Detailed Analysis</div>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Baseline Run #{activeExpDetail.baseline_run_id} vs Candidate Run #{activeExpDetail.candidate_run_id}
              </span>
            </div>
            <div>
              <span className={`badge ${activeExpDetail.verdict === 'PASS' ? 'badge-pass' : (activeExpDetail.verdict === 'FAIL' ? 'badge-fail' : 'badge-review')}`} style={{ fontSize: '1rem', padding: '0.4rem 1rem' }}>
                VERDICT: {activeExpDetail.verdict || 'REVIEW'}
              </span>
            </div>
          </div>

          <div style={{ padding: '1rem', background: 'rgba(255, 255, 255, 0.02)', borderRadius: '8px', border: '1px solid var(--border-color)', marginBottom: '1.5rem' }}>
            <div style={{ fontWeight: 600, fontSize: '0.9rem', marginBottom: '0.3rem' }}>Deployment Gate Reason</div>
            <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              {activeExpDetail.verdict_reason || 'Regression tolerance threshold checked against baseline score.'}
            </div>
          </div>

          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Metric Name</th>
                  <th>Baseline Score (V1)</th>
                  <th>Candidate Score (V2)</th>
                  <th>Metric Delta</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(activeExpDetail.metric_diffs || {}).map(([metric, data]) => {
                  const base = data.baseline !== null ? `${(data.baseline * 100).toFixed(1)}%` : 'N/A';
                  const cand = data.candidate !== null ? `${(data.candidate * 100).toFixed(1)}%` : 'N/A';
                  const delta = data.delta !== null ? (data.delta * 100).toFixed(1) : 0;
                  const deltaStr = data.delta !== null ? `${data.delta >= 0 ? '+' : ''}${delta}%` : '—';
                  const isImproved = data.delta >= 0;

                  return (
                    <tr key={metric}>
                      <td><strong>{metric.toUpperCase()}</strong></td>
                      <td>{base}</td>
                      <td>{cand}</td>
                      <td>
                        <strong style={{ color: isImproved ? 'var(--success)' : 'var(--danger)' }}>
                          {deltaStr}
                        </strong>
                      </td>
                      <td>
                        <span className={`badge ${isImproved ? 'badge-pass' : 'badge-review'}`}>
                          {isImproved ? '▲ IMPROVED' : '▼ REGRESSED'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
