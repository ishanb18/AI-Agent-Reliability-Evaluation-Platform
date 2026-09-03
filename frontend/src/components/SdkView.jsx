import React, { useState, useEffect } from 'react';
import { Terminal, Code, Play, Check, Copy, Cpu, Zap, Key, Layers, ArrowRight, Activity } from 'lucide-react';

export default function SdkView({ user }) {
  const [agents, setAgents] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [prompt, setPrompt] = useState('Explain how multi-model failover works in 2 short bullet points.');
  const [executionMethod, setExecutionMethod] = useState('sdk_trace');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState('python');
  const [copied, setCopied] = useState(false);

  const apiKey = user?.api_key || 'ant_demo_api_key_123456';

  useEffect(() => {
    fetchAgents();
  }, []);

  async function fetchAgents() {
    try {
      const res = await fetch('/agents');
      const data = await res.json();
      setAgents(data);
      if (data.length > 0) {
        setSelectedAgentId(data[0].id);
      }
    } catch (err) {
      console.error('Failed to fetch agents for SDK view:', err);
    }
  }

  async function handleRunPlayground(e) {
    e.preventDefault();
    setLoading(true);
    setResult(null);

    const token = localStorage.getItem('token');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    try {
      const res = await fetch('/sdk/execute-playground', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          method: executionMethod,
          agent_id: selectedAgentId ? parseInt(selectedAgentId) : null,
          prompt: prompt,
        }),
      });

      if (!res.ok) {
        let errMsg = `Server error (${res.status})`;
        try {
          const errData = await res.json();
          errMsg = errData.detail || errMsg;
        } catch {
          errMsg = await res.text() || errMsg;
        }
        setResult({ status: 'error', output: errMsg, provider_used: '-', model_used: '-' });
      } else {
        const data = await res.json();
        setResult(data);
      }
    } catch (err) {
      setResult({ status: 'error', output: err.message, provider_used: '-', model_used: '-' });
    } finally {
      setLoading(false);
    }
  }

  function handleCopy(text) {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const pythonSnippet = `from app.sdk.evalplatform import trace_step, evaluate
import requests

# Set your account API Key
API_KEY = "${apiKey}"
BASE_URL = "http://localhost:8000"

@trace_step(step_name="query_agent", step_type="generation")
def run_my_agent(prompt_text):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    payload = {
        "prompt": prompt_text,
        "provider": "gemini",
        "enable_fallback": True
    }
    response = requests.post(f"{BASE_URL}/gateway/generate", json=payload, headers=headers)
    return response.json()["response"]

# Execute Agent with Tracing
output = run_my_agent("${prompt}")
print("Agent Output:", output)`;

  const curlSnippet = `curl -X POST "http://localhost:8000/gateway/generate" \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "prompt": "${prompt}",
    "provider": "gemini",
    "enable_fallback": true
  }'`;

  const jsSnippet = `const API_KEY = "${apiKey}";

async function queryAgent(promptText) {
  const response = await fetch("http://localhost:8000/gateway/generate", {
    method: "POST",
    headers: {
      "Authorization": \`Bearer \${API_KEY}\`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      prompt: promptText,
      provider: "gemini",
      enable_fallback: true
    })
  });
  
  const data = await response.json();
  console.log("Agent Response:", data.response);
}

queryAgent("${prompt}");`;

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Python SDK & Interactive API Playground</h1>
          <p>Test your AI Agents step-by-step with Python `@trace_step` SDK or direct REST API Endpoints</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'rgba(99, 102, 241, 0.15)', padding: '0.4rem 0.8rem', borderRadius: '8px', border: '1px solid rgba(99, 102, 241, 0.3)' }}>
          <Key size={16} color="var(--primary)" />
          <span style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)', color: 'var(--text-light)' }}>
            API Key: <strong>{apiKey.substring(0, 16)}...</strong>
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginBottom: '2rem' }}>
        {/* Playground Input Card */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Play size={18} color="var(--primary)" /> Interactive Execution Playground
            </div>
          </div>

          <form onSubmit={handleRunPlayground}>
            <div className="form-group">
              <label>Select Target Agent (Optional)</label>
              <select
                className="form-select"
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
              >
                <option value="">-- Direct Model Gateway Call --</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    Agent #{a.id}: {a.name} ({a.provider} / {a.model_name})
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Execution Method</label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginTop: '0.2rem' }}>
                <button
                  type="button"
                  className={`btn ${executionMethod === 'sdk_trace' ? 'btn-primary' : 'btn-secondary'}`}
                  style={{ justifyContent: 'center', fontSize: '0.85rem' }}
                  onClick={() => setExecutionMethod('sdk_trace')}
                >
                  <Code size={16} /> Python SDK Tracing
                </button>
                <button
                  type="button"
                  className={`btn ${executionMethod === 'rest_api' ? 'btn-primary' : 'btn-secondary'}`}
                  style={{ justifyContent: 'center', fontSize: '0.85rem' }}
                  onClick={() => setExecutionMethod('rest_api')}
                >
                  <Zap size={16} /> REST API Endpoint
                </button>
              </div>
            </div>

            <div className="form-group">
              <label>Input Prompt / Query</label>
              <textarea
                className="form-input"
                rows={4}
                required
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Enter prompt for agent evaluation..."
                style={{ fontFamily: 'var(--font-sans)', fontSize: '0.9rem' }}
              />
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading}
              style={{ width: '100%', justifyContent: 'center', padding: '0.75rem' }}
            >
              {loading ? 'Executing Agent Pipeline...' : '▶ Execute Test Request'}
            </button>
          </form>
        </div>

        {/* Playground Live Output Result */}
        <div className="glass-card">
          <div className="card-header">
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Terminal size={18} color="var(--success)" /> Real-Time Response & Step Traces
            </div>
            {result && (
              <span className={`badge ${result.status === 'success' ? 'badge-pass' : 'badge-review'}`}>
                {result.total_latency_ms} ms
              </span>
            )}
          </div>

          {!result && !loading && (
            <div style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
              <Activity size={40} style={{ margin: '0 auto 1rem', opacity: 0.4 }} />
              <div>Click <strong>"Execute Test Request"</strong> to view real-time SDK output and step execution timeline.</div>
            </div>
          )}

          {loading && (
            <div style={{ padding: '3rem 1rem', textAlign: 'center', color: 'var(--text-muted)' }}>
              <div className="spinner" style={{ margin: '0 auto 1rem' }} />
              <div>Running agent execution & step-level tracing...</div>
            </div>
          )}

          {result && !loading && (
            <div>
              <div style={{ background: 'rgba(0,0,0,0.3)', padding: '0.75rem 1rem', borderRadius: '8px', border: '1px solid var(--border-color)', marginBottom: '1rem' }}>
                <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600 }}>
                  Agent Response Output ({result.provider_used} / {result.model_used})
                </div>
                <div style={{ marginTop: '0.5rem', fontSize: '0.9rem', color: '#f8fafc', whiteSpace: 'pre-wrap' }}>
                  {result.output}
                </div>
              </div>

              {result.step_traces && (
                <div>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    <Layers size={16} color="var(--primary)" /> Granular Step Trace Timeline
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    {result.step_traces.map((st, i) => (
                      <div
                        key={i}
                        style={{
                          background: 'rgba(255, 255, 255, 0.03)',
                          border: '1px solid var(--border-color)',
                          borderRadius: '6px',
                          padding: '0.6rem 0.8rem',
                          fontSize: '0.82rem'
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.2rem' }}>
                          <strong style={{ color: 'var(--primary)' }}>
                            Step {i + 1}: {st.step_name} ({st.step_type})
                          </strong>
                          <span style={{ color: 'var(--success)', fontFamily: 'var(--font-mono)' }}>
                            {st.latency_ms} ms
                          </span>
                        </div>
                        {st.result_preview && (
                          <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem', fontFamily: 'var(--font-mono)' }}>
                            {st.result_preview}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Code Snippets Developer Integration Section */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">Integration Code Snippets</div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className={`btn ${activeTab === 'python' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
              onClick={() => setActiveTab('python')}
            >
              Python SDK
            </button>
            <button
              className={`btn ${activeTab === 'curl' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
              onClick={() => setActiveTab('curl')}
            >
              cURL
            </button>
            <button
              className={`btn ${activeTab === 'js' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
              onClick={() => setActiveTab('js')}
            >
              JavaScript
            </button>
          </div>
        </div>

        <div style={{ position: 'relative' }}>
          <pre style={{ background: '#070a12', padding: '1.2rem', borderRadius: '10px', overflowX: 'auto', fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: '#e2e8f0', border: '1px solid var(--border-color)' }}>
            {activeTab === 'python' && pythonSnippet}
            {activeTab === 'curl' && curlSnippet}
            {activeTab === 'js' && jsSnippet}
          </pre>
          <button
            className="btn btn-secondary"
            style={{ position: 'absolute', top: '10px', right: '10px', padding: '0.3rem 0.6rem', fontSize: '0.75rem' }}
            onClick={() => handleCopy(activeTab === 'python' ? pythonSnippet : (activeTab === 'curl' ? curlSnippet : jsSnippet))}
          >
            {copied ? <Check size={14} color="var(--success)" /> : <Copy size={14} />} {copied ? 'Copied!' : 'Copy Code'}
          </button>
        </div>
      </div>
    </div>
  );
}
