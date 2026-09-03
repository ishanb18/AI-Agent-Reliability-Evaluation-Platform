import React, { useEffect, useState } from 'react';
import { Bot, TestTube, LineChart, FlaskConical, Cpu, ArrowUpRight } from 'lucide-react';

export default function DashboardView({ setActiveTab, setSelectedRunId }) {
  const [stats, setStats] = useState({ agents: 0, suites: 0, runs: 0, experiments: 0 });
  const [providers, setProviders] = useState([]);
  const [recentRuns, setRecentRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      try {
        const [agentsRes, suitesRes, runsRes, expRes, statusRes] = await Promise.all([
          fetch('/agents').then(r => r.json()).catch(() => []),
          fetch('/test-suites').then(r => r.json()).catch(() => []),
          fetch('/evaluations').then(r => r.json()).catch(() => []),
          fetch('/experiments').then(r => r.json()).catch(() => []),
          fetch('/providers/status').then(r => r.json()).catch(() => ({ providers: [] }))
        ]);

        setStats({
          agents: agentsRes.length || 0,
          suites: suitesRes.length || 0,
          runs: runsRes.length || 0,
          experiments: expRes.length || 0,
        });
        setProviders(statusRes.providers || []);
        setRecentRuns(runsRes.slice(0, 5) || []);
      } catch (err) {
        console.error('Failed loading dashboard data:', err);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Platform Overview</h1>
          <p>Real-time reliability metrics, provider gateway health, and evaluation runs</p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="btn btn-primary" onClick={() => setActiveTab('agents')}>
            + Register Agent
          </button>
          <button className="btn btn-secondary" onClick={() => setActiveTab('evaluations')}>
            ▶ Run Evaluation
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="stats-grid">
        <div className="stat-box">
          <div className="stat-title">Registered Agents</div>
          <div className="stat-num">{stats.agents}</div>
        </div>
        <div className="stat-box">
          <div className="stat-title">Test Suites</div>
          <div className="stat-num">{stats.suites}</div>
        </div>
        <div className="stat-box">
          <div className="stat-title">Total Eval Runs</div>
          <div className="stat-num">{stats.runs}</div>
        </div>
        <div className="stat-box">
          <div className="stat-title">Experiments</div>
          <div className="stat-num">{stats.experiments}</div>
        </div>
      </div>

      {/* Model Gateway Status */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Cpu size={18} color="#6366f1" /> Model Gateway Providers
          </div>
          <span className="badge badge-info">Automatic Failover Active</span>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>Provider Name</th>
                <th>Status</th>
                <th>Daily Quota / Capacity</th>
                <th>Failover Role</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => (
                <tr key={p.name}>
                  <td><strong>{p.name.toUpperCase()}</strong></td>
                  <td>
                    <span className={`badge ${p.available ? 'badge-pass' : 'badge-fail'}`}>
                      {p.available ? '● HEALTHY' : '○ UNAVAILABLE'}
                    </span>
                  </td>
                  <td>{p.quota_estimate || 'Unlimited Local'}</td>
                  <td>{p.supports_fallback ? 'Fallback Ready' : 'Primary Gateway'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Runs Table */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">Recent Evaluation Activity</div>
          <button className="btn btn-secondary" style={{ fontSize: '0.8rem' }} onClick={() => setActiveTab('evaluations')}>
            View All
          </button>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Agent</th>
                <th>Status</th>
                <th>Passed / Total</th>
                <th>Avg Score</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {recentRuns.length > 0 ? (
                recentRuns.map((r) => {
                  const score = r.avg_score !== null ? `${(r.avg_score * 100).toFixed(1)}%` : 'N/A';
                  return (
                    <tr key={r.id}>
                      <td><strong>#{r.id}</strong></td>
                      <td>Agent #{r.agent_id}</td>
                      <td>
                        <span className={`badge ${r.status === 'completed' ? 'badge-pass' : 'badge-review'}`}>
                          {r.status}
                        </span>
                      </td>
                      <td>{r.passed_cases} / {r.total_cases}</td>
                      <td><strong>{score}</strong></td>
                      <td>
                        <button
                          className="btn btn-secondary"
                          style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
                          onClick={() => {
                            setSelectedRunId(r.id);
                            setActiveTab('evaluations');
                          }}
                        >
                          Details <ArrowUpRight size={12} />
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan="6" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                    No evaluation runs executed yet. Trigger one above!
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
