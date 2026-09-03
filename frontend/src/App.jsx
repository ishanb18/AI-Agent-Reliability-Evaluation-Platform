import React, { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import DashboardView from './components/DashboardView';
import AgentsView from './components/AgentsView';
import SuitesView from './components/SuitesView';
import EvaluationsView from './components/EvaluationsView';
import LiveStreamView from './components/LiveStreamView';
import ExperimentsView from './components/ExperimentsView';
import SdkView from './components/SdkView';
import LoginView from './components/LoginView';

export default function App() {
  const [user, setUser] = useState(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [selectedExpId, setSelectedExpId] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);

  useEffect(() => {
    // Check saved session token on load
    const token = localStorage.getItem('token');
    if (token) {
      fetch('/auth/me', {
        headers: { Authorization: `Bearer ${token}` }
      })
        .then(r => {
          if (r.ok) return r.json();
          throw new Error('Token expired');
        })
        .then(userData => {
          setUser(userData);
        })
        .catch(() => {
          localStorage.removeItem('token');
          localStorage.removeItem('user');
          setUser(null);
        })
        .finally(() => setCheckingAuth(false));
    } else {
      setCheckingAuth(false);
    }
  }, []);

  function handleLogout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    setUser(null);
  }

  if (checkingAuth) {
    return (
      <div style={{ display: 'flex', height: '100vh', width: '100vw', alignItems: 'center', justifyContent: 'center', background: '#090d16', color: '#94a3b8' }}>
        Authenticating session...
      </div>
    );
  }

  if (!user) {
    return <LoginView onLoginSuccess={(u) => setUser(u)} />;
  }

  return (
    <div className="app-layout">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        user={user}
        onLogout={handleLogout}
      />
      <main className="main-wrapper">
        {activeTab === 'dashboard' && (
          <DashboardView setActiveTab={setActiveTab} setSelectedRunId={setSelectedRunId} />
        )}
        {activeTab === 'agents' && (
          <AgentsView setActiveTab={setActiveTab} setSelectedExpId={setSelectedExpId} />
        )}
        {activeTab === 'suites' && <SuitesView />}
        {activeTab === 'evaluations' && (
          <EvaluationsView
            selectedRunId={selectedRunId}
            setSelectedRunId={setSelectedRunId}
            setActiveTab={setActiveTab}
          />
        )}
        {activeTab === 'live' && <LiveStreamView selectedRunId={selectedRunId} />}
        {activeTab === 'experiments' && (
          <ExperimentsView
            selectedExpId={selectedExpId}
            setSelectedExpId={setSelectedExpId}
            setActiveTab={setActiveTab}
          />
        )}
        {activeTab === 'sdk' && <SdkView user={user} />}
      </main>
    </div>
  );
}
