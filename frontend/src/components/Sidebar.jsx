import React from 'react';
import { LayoutDashboard, Bot, TestTube, LineChart, FlaskConical, Code, LogOut, Key } from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab, user, onLogout }) {
  const menuItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'agents', label: 'Agents & Versions', icon: Bot },
    { id: 'suites', label: 'Test Suites', icon: TestTube },
    { id: 'evaluations', label: 'Eval Runs', icon: LineChart },
    { id: 'experiments', label: 'Experiments', icon: FlaskConical },
    { id: 'sdk', label: 'SDK & API Sandbox', icon: Code },
  ];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo">⚡</div>
        <div className="brand-text">
          <h2>Antigravity Eval</h2>
          <span>AI Reliability Platform</span>
        </div>
      </div>

      <ul className="nav-menu">
        {menuItems.map((item) => {
          const Icon = item.icon;
          return (
            <li key={item.id}>
              <button
                className={`nav-button ${activeTab === item.id ? 'active' : ''}`}
                onClick={() => setActiveTab(item.id)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            </li>
          );
        })}
      </ul>

      {/* Logged in User Profile Footer */}
      {user && (
        <div style={{ marginTop: 'auto', paddingTop: '1.5rem', borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.75rem' }}>
            <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'var(--primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '0.9rem' }}>
              {user.username.charAt(0).toUpperCase()}
            </div>
            <div style={{ overflow: 'hidden' }}>
              <div style={{ fontWeight: 600, fontSize: '0.88rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {user.username}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {user.email}
              </div>
            </div>
          </div>

          <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '0.4rem 0.6rem', borderRadius: '6px', fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', marginBottom: '0.75rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span>Key: {user.api_key ? user.api_key.substring(0, 10) + '...' : 'ant_...'}</span>
            <Key size={12} />
          </div>

          <button
            className="nav-button"
            style={{ color: '#ef4444', padding: '0.5rem 0.75rem' }}
            onClick={onLogout}
          >
            <LogOut size={16} />
            <span>Sign Out</span>
          </button>
        </div>
      )}
    </aside>
  );
}
