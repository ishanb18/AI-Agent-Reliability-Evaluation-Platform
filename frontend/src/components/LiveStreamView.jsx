import React, { useEffect, useState, useRef } from 'react';
import { Radio, Activity, CheckCircle2, AlertTriangle } from 'lucide-react';

export default function LiveStreamView({ selectedRunId }) {
  const [runId, setRunId] = useState(selectedRunId || '');
  const [logs, setLogs] = useState([]);
  const [completedCases, setCompletedCases] = useState(0);
  const [totalCases, setTotalCases] = useState(0);
  const [avgScore, setAvgScore] = useState(null);
  const [status, setStatus] = useState('connecting');
  const eventSourceRef = useRef(null);

  useEffect(() => {
    if (selectedRunId) {
      setRunId(selectedRunId);
      startStreaming(selectedRunId);
    }

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [selectedRunId]);

  function startStreaming(id) {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    setLogs([]);
    setCompletedCases(0);
    setTotalCases(0);
    setAvgScore(null);
    setStatus('streaming');

    const scores = [];
    const es = new EventSource(`/evaluations/stream/${id}`);
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        const timeStr = new Date().toLocaleTimeString();

        if (data.event === 'started') {
          setLogs(prev => [...prev, { time: timeStr, text: `Evaluation run #${id} started`, type: 'info' }]);
        } else if (data.event === 'case_done') {
          setCompletedCases(data.case);
          setTotalCases(data.total);

          if (data.score !== null && data.score !== undefined) {
            scores.push(data.score);
            const currentAvg = scores.reduce((a, b) => a + b, 0) / scores.length;
            setAvgScore((currentAvg * 100).toFixed(1));
          }

          const scoreStr = data.score !== null && data.score !== undefined ? `${(data.score * 100).toFixed(0)}%` : 'FAILED';
          setLogs(prev => [
            ...prev,
            {
              time: timeStr,
              text: `Case ${data.case}/${data.total} finished — Score: ${scoreStr} (${data.latency_ms || 0}ms) — Prompt: "${data.input_preview || ''}"`,
              type: data.status === 'success' ? 'success' : 'error'
            }
          ]);
        } else if (data.event === 'run_complete' || data.event === 'completed') {
          setStatus('completed');
          if (data.avg_score !== undefined) {
            setAvgScore((data.avg_score * 100).toFixed(1));
          }
          setLogs(prev => [...prev, { time: timeStr, text: `🎉 Run #${id} completed completely! Passed: ${data.passed || 0}, Failed: ${data.failed || 0}`, type: 'success' }]);
          es.close();
        } else if (data.event === 'error' || data.event === 'failed') {
          setStatus('failed');
          setLogs(prev => [...prev, { time: timeStr, text: `Error: ${data.message || 'Run failed'}`, type: 'error' }]);
          es.close();
        }
      } catch (err) {
        console.error('Failed parsing event:', err);
      }
    };

    es.onerror = (err) => {
      console.error('SSE error:', err);
      setStatus('disconnected');
      es.close();
    };
  }

  const progressPct = totalCases > 0 ? Math.round((completedCases / totalCases) * 100) : (status === 'completed' ? 100 : 0);

  return (
    <div className="view-container">
      <div className="header-row">
        <div>
          <h1>Live Real-Time SSE Progress Stream</h1>
          <p>Asynchronous evaluation telemetry stream via Server-Sent Events (SSE)</p>
        </div>
        <div>
          <span className={`badge ${status === 'completed' ? 'badge-pass' : (status === 'failed' ? 'badge-fail' : 'badge-info')}`}>
            {status === 'streaming' ? '📡 STREAMING LIVE' : status.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Progress Bar Header */}
      <div className="glass-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <div style={{ fontWeight: 600 }}>
            Progress: {completedCases} / {totalCases} cases completed ({progressPct}%)
          </div>
          <div style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--primary)' }}>
            Avg Score: {avgScore ? `${avgScore}%` : '—'}
          </div>
        </div>
        <div style={{ height: '12px', background: 'var(--border-color)', borderRadius: '6px', overflow: 'hidden' }}>
          <div
            style={{
              width: `${progressPct}%`,
              height: '100%',
              background: 'linear-gradient(90deg, #6366f1, #10b981)',
              transition: 'width 0.3s ease',
              borderRadius: '6px'
            }}
          />
        </div>
      </div>

      {/* Live Event Console */}
      <div className="glass-card">
        <div className="card-header">
          <div className="card-title">Real-Time Event Stream Log</div>
        </div>
        <div style={{ background: '#080c14', border: '1px solid var(--border-color)', padding: '1.25rem', borderRadius: '10px', height: '380px', overflowY: 'auto', fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}>
          {logs.length > 0 ? (
            logs.map((l, i) => (
              <div key={i} style={{ marginBottom: '0.4rem', color: l.type === 'success' ? '#10b981' : (l.type === 'error' ? '#ef4444' : '#94a3b8') }}>
                <span style={{ color: 'var(--text-dim)' }}>[{l.time}]</span> {l.text}
              </div>
            ))
          ) : (
            <div style={{ color: 'var(--text-dim)' }}>Awaiting events from SSE endpoint GET /evaluations/stream/{runId || '...'}</div>
          )}
        </div>
      </div>
    </div>
  );
}
