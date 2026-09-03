import React, { useEffect, useState } from 'react';
import { Bot, GitFork, ArrowLeftRight, Plus } from 'lucide-react';

export default function AgentsView({ setActiveTab, setSelectedExpId }) {
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [versions, setVersions] = useState([]);
  const [suites, setSuites] = useState([]);

  // Modal states
  const [showAgentModal, setShowAgentModal] = useState(false);
  const [showVersionModal, setShowVersionModal] = useState(false);
  const [showCompareModal, setShowCompareModal] = useState(false);

  // Forms
  const [agentForm, setAgentForm] = useState({ name: '', endpoint: '', framework: 'custom', model: 'gpt-4o' });
  const [versionForm, setVersionForm] = useState({ version: '', model: '', endpoint: '', notes: '' });
  const [compareForm, setCompareForm] = useState({ baseVer: '', candVer: '', suiteId: '' });

  useEffect(() => {
    fetchAgents();
    fetch('/test-suites').then(r => r.json()).then(setSuites).catch(() => []);
  }, []);

  async function fetchAgents() {
    try {
      const res = await fetch('/agents');
      const data = await res.json();
      setAgents(data);
      if (data.length > 0 && !selectedAgent) {
        selectAgent(data[0]);
      }
    } catch (err) {
      console.error('Failed fetching agents:', err);
    }
  }

  async function selectAgent(agent) {
    setSelectedAgent(agent);
    try {
      const res = await fetch(`/agents/${agent.id}/versions`);
      const data = await res.json();
      setVersions(data);

      if (data.length > 0) {
        setCompareForm({
          baseVer: data[0].id,
          candVer: data[1] ? data[1].id : data[0].id,
          suiteId: suites[0] ? suites[0].id : ''
        });
      }
    } catch (err) {
      console.error('Failed fetching versions:', err);
    }
  }

  async function handleRegisterAgent(e) {
    e.preventDefault();
    try {
      const res = await fetch('/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...agentForm, connection_type: 'rest_api' }),
      });
      if (res.ok) {
        setShowAgentModal(false);
        setAgentForm({ name: '', endpoint: '', framework: 'custom', model: 'gpt-4o' });
        fetchAgents();
      }
    } catch (err) {
      alert('Error saving agent: ' + err.message);
    }
  }

  async function handleForkVersion(e) {
    e.preventDefault();
    if (!selectedAgent) return;
    try {
      const res = await fetch(`/agents/${selectedAgent.id}/versions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: versionForm.version,
          model: versionForm.model || null,
          endpoint: versionForm.endpoint || null,
          notes: versionForm.notes || null,
        }),
      });
      if (res.ok) {
        setShowVersionModal(false);
        setVersionForm({ version: '', model: '', endpoint: '', notes: '' });
        selectAgent(selectedAgent);
      }
    } catch (err) {
      alert('Error forking version: ' + err.message);
    }
  }

  async function handleRunCompare(e) {
    e.preventDefault();
    try {
      const res = await fetch(`/agents/${selectedAgent.id}/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          baseline_version_id: parseInt(compareForm.baseVer),
          candidate_version_id: parseInt(compareForm.candVer),
          suite_id: parseInt(compareForm.suiteId)
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setShowCompareModal(false);
        setSelectedExpId(data.experiment_id);
        setActiveTab('experiments');
      }
    } catch (err) {
      alert('Comparison failed: ' + err.message);
    }
  }

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Agent Versioning Workspace</h1>
          <p>Manage agent lineages, fork version trees, and run V1 vs V2 comparisons</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowAgentModal(true)}>
          <Plus size={16} /> Register Agent
        </button>
      </div>

      {/* Agents Table */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">Registered Agents</div>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Framework</th>
                <th>Default Model</th>
                <th>Endpoint</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id}>
                  <td><strong>#{a.id}</strong></td>
                  <td><strong>{a.name}</strong></td>
                  <td><span className="badge badge-info">{a.framework || 'custom'}</span></td>
                  <td>{a.model || 'gpt-4o'}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{a.endpoint || 'Python SDK'}</td>
                  <td>
                    <button
                      className="btn btn-secondary"
                      style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
                      onClick={() => selectAgent(a)}
                    >
                      View Version Lineage ➔
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Versions Section */}
      {selectedAgent && (
        <div className="glass-card">
          <div className="card-header">
            <div>
              <div className="card-title">Version Lineage: {selectedAgent.name} (Agent #{selectedAgent.id})</div>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Parent config model: {selectedAgent.model || 'gpt-4o'}
              </span>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-secondary" onClick={() => setShowCompareModal(true)}>
                <ArrowLeftRight size={14} /> Quick Version Compare
              </button>
              <button className="btn btn-primary" onClick={() => setShowVersionModal(true)}>
                <GitFork size={14} /> Fork New Version
              </button>
            </div>
          </div>

          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Version ID</th>
                  <th>Tag</th>
                  <th>Model String</th>
                  <th>Latest Score</th>
                  <th>Runs Count</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {versions.length > 0 ? (
                  versions.map((v) => {
                    const score = v.latest_eval_score !== null ? `${(v.latest_eval_score * 100).toFixed(1)}%` : 'Not run';
                    return (
                      <tr key={v.id}>
                        <td><strong>#{v.id}</strong></td>
                        <td><span className="badge badge-pass">{v.version}</span></td>
                        <td>{v.model || 'inherited'}</td>
                        <td><strong>{score}</strong></td>
                        <td>{v.total_eval_runs}</td>
                        <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>{v.notes || '—'}</td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="6" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                      No custom versions forked yet. Fork a new version to test prompt/model changes!
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Modal 1: Register Agent */}
      {showAgentModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h2 style={{ marginBottom: '1rem' }}>Register Parent Agent</h2>
            <form onSubmit={handleRegisterAgent}>
              <div className="form-group">
                <label>Agent Name *</label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={agentForm.name}
                  onChange={(e) => setAgentForm({ ...agentForm, name: e.target.value })}
                  placeholder="e.g. Customer Support Bot"
                />
              </div>
              <div className="form-group">
                <label>REST Endpoint URL *</label>
                <input
                  type="url"
                  className="form-input"
                  required
                  value={agentForm.endpoint}
                  onChange={(e) => setAgentForm({ ...agentForm, endpoint: e.target.value })}
                  placeholder="http://localhost:8001/chat"
                />
              </div>
              <div className="form-group">
                <label>Framework</label>
                <input
                  type="text"
                  className="form-input"
                  value={agentForm.framework}
                  onChange={(e) => setAgentForm({ ...agentForm, framework: e.target.value })}
                  placeholder="langgraph, crewai, or custom"
                />
              </div>
              <div className="form-group">
                <label>LLM Model</label>
                <input
                  type="text"
                  className="form-input"
                  value={agentForm.model}
                  onChange={(e) => setAgentForm({ ...agentForm, model: e.target.value })}
                  placeholder="gpt-4o, gemini-1.5-flash"
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowAgentModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Save Agent</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal 2: Fork Version */}
      {showVersionModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h2 style={{ marginBottom: '1rem' }}>Fork Agent Version</h2>
            <form onSubmit={handleForkVersion}>
              <div className="form-group">
                <label>Version Tag *</label>
                <input
                  type="text"
                  className="form-input"
                  required
                  value={versionForm.version}
                  onChange={(e) => setVersionForm({ ...versionForm, version: e.target.value })}
                  placeholder="v2, gpt4o-mini-test"
                />
              </div>
              <div className="form-group">
                <label>Model String Override</label>
                <input
                  type="text"
                  className="form-input"
                  value={versionForm.model}
                  onChange={(e) => setVersionForm({ ...versionForm, model: e.target.value })}
                  placeholder="gpt-4o-mini (leave blank to inherit)"
                />
              </div>
              <div className="form-group">
                <label>Change Notes</label>
                <textarea
                  className="form-input"
                  rows="2"
                  value={versionForm.notes}
                  onChange={(e) => setVersionForm({ ...versionForm, notes: e.target.value })}
                  placeholder="Testing cheaper model for simple queries"
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowVersionModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Fork Version</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal 3: Quick Version Compare */}
      {showCompareModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h2 style={{ marginBottom: '1rem' }}>Quick Version Compare (V1 vs V2)</h2>
            <form onSubmit={handleRunCompare}>
              <div className="form-group">
                <label>Baseline Version (V1) *</label>
                <select
                  className="form-input"
                  required
                  value={compareForm.baseVer}
                  onChange={(e) => setCompareForm({ ...compareForm, baseVer: e.target.value })}
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>{v.version} ({v.model || 'inherited'})</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>Candidate Version (V2) *</label>
                <select
                  className="form-input"
                  required
                  value={compareForm.candVer}
                  onChange={(e) => setCompareForm({ ...compareForm, candVer: e.target.value })}
                >
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
                  value={compareForm.suiteId}
                  onChange={(e) => setCompareForm({ ...compareForm, suiteId: e.target.value })}
                >
                  {suites.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.case_count || 0} cases)</option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowCompareModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Run Version Comparison</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
