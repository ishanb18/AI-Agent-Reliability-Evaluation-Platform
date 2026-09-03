import React, { useEffect, useState } from 'react';
import { TestTube, Sparkles, Plus, CheckCircle2 } from 'lucide-react';

export default function SuitesView() {
  const [suites, setSuites] = useState([]);
  const [selectedSuite, setSelectedSuite] = useState(null);
  const [cases, setCases] = useState([]);
  const [showGenModal, setShowGenModal] = useState(false);

  const [genForm, setGenForm] = useState({ category: 'edge_cases', count: 3 });

  useEffect(() => {
    fetchSuites();
  }, []);

  async function fetchSuites() {
    try {
      const res = await fetch('/test-suites');
      const data = await res.json();
      setSuites(data);
      if (data.length > 0 && !selectedSuite) {
        selectSuite(data[0]);
      }
    } catch (err) {
      console.error('Failed fetching suites:', err);
    }
  }

  async function selectSuite(suite) {
    setSelectedSuite(suite);
    try {
      const res = await fetch(`/test-suites/${suite.id}`);
      const data = await res.json();
      setCases(data.test_cases || data.cases || []);
    } catch (err) {
      console.error('Failed fetching suite cases:', err);
    }
  }

  async function handleSeedSuite() {
    try {
      const res = await fetch('/test-suites/seed', { method: 'POST' });
      if (res.ok) {
        alert('Standard benchmark suite seeded successfully!');
        fetchSuites();
      }
    } catch (err) {
      alert('Failed seeding suite: ' + err.message);
    }
  }

  async function handleGenerateCases(e) {
    e.preventDefault();
    if (!selectedSuite) return;
    try {
      const mode = genForm.category === 'edge_cases' ? 'edge' : genForm.category;
      const res = await fetch(`/test-suites/${selectedSuite.id}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: mode,
          count: parseInt(genForm.count),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setShowGenModal(false);
        alert(`Successfully generated ${data.generated_count || genForm.count} test cases!`);
        selectSuite(selectedSuite);
      } else {
        alert('Generation failed: ' + (data.detail || 'Error generating cases'));
      }
    } catch (err) {
      alert('Generation failed: ' + err.message);
    }
  }

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Test Suite & Case Manager</h1>
          <p>Create benchmark suites, generate AI edge/security test cases, and review test cases</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-secondary" onClick={handleSeedSuite}>
            🌱 Seed Standard Suite
          </button>
          <button className="btn btn-primary" onClick={() => setShowGenModal(true)}>
            <Sparkles size={16} /> AI Test Generator
          </button>
        </div>
      </div>

      {/* Suites Table */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">Test Benchmark Suites</div>
        </div>
        <div className="table-responsive">
          <table>
            <thead>
              <tr>
                <th>Suite ID</th>
                <th>Name</th>
                <th>Category</th>
                <th>Case Count</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {suites.map((s) => (
                <tr key={s.id}>
                  <td><strong>#{s.id}</strong></td>
                  <td><strong>{s.name}</strong></td>
                  <td><span className="badge badge-info">{s.category || 'general'}</span></td>
                  <td><strong>{s.case_count || s.cases?.length || 0} cases</strong></td>
                  <td>
                    <button
                      className="btn btn-secondary"
                      style={{ fontSize: '0.75rem', padding: '0.25rem 0.5rem' }}
                      onClick={() => selectSuite(s)}
                    >
                      Inspect Cases ➔
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Cases Table */}
      {selectedSuite && (
        <div className="glass-card">
          <div className="card-header">
            <div>
              <div className="card-title">Cases in Suite: {selectedSuite.name} (Suite #{selectedSuite.id})</div>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                Total cases: {cases.length}
              </span>
            </div>
          </div>

          <div className="table-responsive">
            <table>
              <thead>
                <tr>
                  <th>Case ID</th>
                  <th>Category</th>
                  <th>Input Prompt</th>
                  <th>Expected Output</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {cases.length > 0 ? (
                  cases.map((c) => (
                    <tr key={c.id}>
                      <td><strong>#{c.id}</strong></td>
                      <td><span className="badge badge-info">{c.category || 'general'}</span></td>
                      <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', maxWidth: '300px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {c.input}
                      </td>
                      <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem', maxWidth: '250px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {c.expected_answer || '—'}
                      </td>
                      <td>
                        <span className={`badge ${c.is_approved ? 'badge-pass' : 'badge-review'}`}>
                          {c.is_approved ? 'ACTIVE' : 'PENDING'}
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="5" style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                      No cases in this suite yet. Click "AI Test Generator" above!
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* AI Test Case Generator Modal */}
      {showGenModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h2 style={{ marginBottom: '1rem' }}>AI Test Case Generator</h2>
            <form onSubmit={handleGenerateCases}>
              <div className="form-group">
                <label>Target Test Suite</label>
                <input
                  type="text"
                  className="form-input"
                  disabled
                  value={selectedSuite ? `${selectedSuite.name} (#${selectedSuite.id})` : ''}
                />
              </div>
              <div className="form-group">
                <label>Generation Category *</label>
                <select
                  className="form-input"
                  value={genForm.category}
                  onChange={(e) => setGenForm({ ...genForm, category: e.target.value })}
                >
                  <option value="edge_cases">Edge Cases (unusual queries)</option>
                  <option value="adversarial">Adversarial & Trick Questions</option>
                  <option value="security">Security (Prompt Injection, PII Leaks)</option>
                </select>
              </div>
              <div className="form-group">
                <label>Number of Test Cases</label>
                <input
                  type="number"
                  className="form-input"
                  min="1"
                  max="10"
                  value={genForm.count}
                  onChange={(e) => setGenForm({ ...genForm, count: e.target.value })}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.5rem' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowGenModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Generate Cases</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
