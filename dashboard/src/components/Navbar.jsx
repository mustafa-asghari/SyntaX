import React from 'react';
import { Activity, Bell, Settings, User } from 'lucide-react';

const Navbar = () => {
    return (
        <nav style={{
            borderBottom: '1px solid var(--border-subtle)',
            padding: '16px 0',
            background: 'rgba(5, 5, 5, 0.8)',
            backdropFilter: 'blur(10px)',
            position: 'sticky',
            top: 0,
            zIndex: 50
        }}>
            <div className="container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{
                        width: '36px',
                        height: '36px',
                        background: 'var(--accent-gradient)',
                        borderRadius: '10px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: 'white',
                        boxShadow: '0 4px 12px rgba(59, 130, 246, 0.3)'
                    }}>
                        <Activity size={20} strokeWidth={2.5} />
                    </div>
                    <h1 style={{ fontSize: '1.25rem', fontWeight: '700', letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
                        SyntaX<span style={{ color: 'var(--text-tertiary)', fontWeight: '400', marginLeft: '6px' }}>Monitor</span>
                    </h1>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                    <div className="status-badge" style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        fontSize: '0.85rem',
                        color: 'var(--success)',
                        background: 'rgba(16, 185, 129, 0.1)',
                        padding: '6px 12px',
                        borderRadius: '20px',
                        border: '1px solid rgba(16, 185, 129, 0.2)'
                    }}>
                        <div className="status-dot active"></div>
                        <span style={{ fontWeight: 500 }}>System Live</span>
                    </div>

                    <div style={{ width: '1px', height: '24px', background: 'var(--border-subtle)' }}></div>

                    <div style={{ display: 'flex', gap: '8px' }}>
                        <button className="icon-btn"><Bell size={18} /></button>
                        <button className="icon-btn"><Settings size={18} /></button>
                        <button className="profile-btn" style={{ marginLeft: '8px' }}>
                            <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: '#333', overflow: 'hidden' }}>
                                <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Felix" alt="User" style={{ width: '100%', height: '100%' }} />
                            </div>
                        </button>
                    </div>
                </div>
            </div>
            <style>{`
        .icon-btn {
          background: transparent;
          border: none;
          color: var(--text-secondary);
          cursor: pointer;
          padding: 8px;
          border-radius: 8px;
          transition: all 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .icon-btn:hover {
          background: var(--bg-card-hover);
          color: var(--text-primary);
        }
      `}</style>
        </nav>
    );
};

export default Navbar;
