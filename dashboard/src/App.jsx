import React from 'react';
import Navbar from './components/Navbar';
import SpeedChart from './components/SpeedChart';
import { ArrowUp, ArrowDown, Zap, Wifi, Server, Database } from 'lucide-react';

function App() {
  return (
    <div className="app">
      <Navbar />

      <main className="container" style={{ padding: '32px 24px', paddingBottom: '60px' }}>
        <div style={{ marginBottom: '32px' }}>
          <h2 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '8px' }}>Performance Overview</h2>
          <p style={{ color: 'var(--text-secondary)' }}>Real-time metrics for API request latency and system health.</p>
        </div>

        <div className="grid-dashboard">
          {/* Main Chart Section */}
          <div className="main-chart-area">
            <SpeedChart />
          </div>

          {/* Side Stats Section */}
          <div className="side-stats-area">
            {/* Global Speed Score */}
            <div className="card gradient-card" style={{ marginBottom: '24px', background: 'var(--accent-gradient)', border: 'none' }}>
              <div style={{ position: 'relative', zIndex: 1 }}>
                <h3 style={{ fontSize: '0.9rem', opacity: 0.9, marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Global Speed Score</h3>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                  <span style={{ fontSize: '3rem', fontWeight: 800 }}>98</span>
                  <span style={{ fontSize: '1.25rem', opacity: 0.8 }}>/100</span>
                </div>
                <div style={{ marginTop: '16px', display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(255,255,255,0.2)', padding: '8px 12px', borderRadius: '8px', width: 'fit-content' }}>
                  <ArrowUp size={16} />
                  <span style={{ fontWeight: 600 }}>+12%</span>
                  <span style={{ fontSize: '0.85rem', opacity: 0.9 }}>Performance Boost</span>
                </div>
              </div>
              {/* Background Decoration */}
              <div style={{ position: 'absolute', top: '-20%', right: '-10%', opacity: 0.2 }}>
                <Zap size={140} />
              </div>
            </div>

            {/* Improvement Stat */}
            <div className="card" style={{ marginBottom: '24px' }}>
              <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>Comparisons (vs Last Week)</h3>

              <div className="comparison-item">
                <div className="icon-box" style={{ background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-primary)' }}><Server size={18} /></div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.9rem', fontWeight: 500 }}>Server Response</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Global CDN Nodes</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--success)' }}>-15ms</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Faster</div>
                </div>
              </div>

              <div className="comparison-item">
                <div className="icon-box" style={{ background: 'rgba(139, 92, 246, 0.1)', color: 'var(--accent-secondary)' }}><Database size={18} /></div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.9rem', fontWeight: 500 }}>Query Time</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>SyntaX DB Cluster</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--success)' }}>-8%</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Optimized</div>
                </div>
              </div>
            </div>

            {/* Quick Actions / Status */}
            <div className="card">
              <h3 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '16px' }}>Active Regions</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div className="region-bar">
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                    <span>US East (N. Virginia)</span>
                    <span style={{ color: 'var(--success)' }}>24ms</span>
                  </div>
                  <div className="progress-bg"><div className="progress-fill" style={{ width: '90%' }}></div></div>
                </div>
                <div className="region-bar">
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                    <span>EU West (London)</span>
                    <span style={{ color: 'var(--success)' }}>42ms</span>
                  </div>
                  <div className="progress-bg"><div className="progress-fill" style={{ width: '75%' }}></div></div>
                </div>
                <div className="region-bar">
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '4px' }}>
                    <span>Asia Pacific (Tokyo)</span>
                    <span style={{ color: 'var(--warning)' }}>112ms</span>
                  </div>
                  <div className="progress-bg"><div className="progress-fill warning" style={{ width: '45%' }}></div></div>
                </div>
              </div>
            </div>

          </div>
        </div>

      </main>

      <style>{`
        .grid-dashboard {
          display: grid;
          grid-template-columns: 2fr 1fr;
          gap: 24px;
        }

        @media (max-width: 900px) {
          .grid-dashboard {
            grid-template-columns: 1fr;
          }
        }

        .comparison-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px 0;
          border-bottom: 1px solid var(--border-subtle);
        }
        .comparison-item:last-child {
          border-bottom: none;
        }

        .icon-box {
          width: 36px; 
          height: 36px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
        }

        .region-bar .progress-bg {
          width: 100%;
          height: 6px;
          background: var(--bg-primary);
          border-radius: 3px;
          overflow: hidden;
        }

        .region-bar .progress-fill {
          height: 100%;
          background: var(--accent-primary);
          border-radius: 3px;
        }
        
        .region-bar .progress-fill.warning {
          background: var(--warning);
        }
      `}</style>
    </div>
  );
}

export default App;
