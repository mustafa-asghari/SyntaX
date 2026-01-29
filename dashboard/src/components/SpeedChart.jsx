import React, { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { Zap, Clock, ArrowDown, ArrowUp, Activity } from 'lucide-react';

const generateDataPoint = (prevData) => {
    const now = new Date();
    const time = now.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    // Simulate latency between 40ms and 150ms with some random spikes
    let latency = Math.floor(Math.random() * (120 - 40 + 1)) + 40;

    if (Math.random() > 0.9) {
        latency += Math.floor(Math.random() * 100); // Random spike
    }

    return {
        time,
        latency,
        timestamp: now.getTime()
    };
};

const SpeedChart = () => {
    const [data, setData] = useState([]);
    const [currentLatency, setCurrentLatency] = useState(0);
    const [avgLatency, setAvgLatency] = useState(0);

    useEffect(() => {
        // Initial data
        const initialData = [];
        for (let i = 20; i > 0; i--) {
            initialData.push(generateDataPoint());
        }
        setData(initialData);

        const interval = setInterval(() => {
            setData(prev => {
                const newPoint = generateDataPoint();
                setCurrentLatency(newPoint.latency);

                const newData = [...prev, newPoint];
                if (newData.length > 50) newData.shift(); // Keep last 50 points

                // precise average
                const sum = newData.reduce((acc, curr) => acc + curr.latency, 0);
                setAvgLatency(Math.round(sum / newData.length));

                return newData;
            });
        }, 1000);

        return () => clearInterval(interval);
    }, []);

    return (
        <div className="card" style={{ height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '24px' }}>
                <div>
                    <h3 style={{ fontSize: '1.1rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Activity size={18} color="var(--accent-primary)" />
                        Real-time Latency
                    </h3>
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
                        Monitoring request response times over the last 60 seconds
                    </p>
                </div>

                <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '2.5rem', fontWeight: 700, lineHeight: 1, letterSpacing: '-1px' }}>
                        {currentLatency}<span style={{ fontSize: '1rem', color: 'var(--text-tertiary)', fontWeight: 500, marginLeft: '4px' }}>ms</span>
                    </div>
                    <div style={{ fontSize: '0.85rem', color: currentLatency < 100 ? 'var(--success)' : 'var(--warning)', marginTop: '4px', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '4px' }}>
                        {currentLatency < 100 ? <Zap size={14} /> : <Clock size={14} />}
                        {currentLatency < 100 ? 'Excellent' : 'Fair'} Speed
                    </div>
                </div>
            </div>

            <div style={{ width: '100%', height: '300px' }}>
                <ResponsiveContainer>
                    <AreaChart data={data}>
                        <defs>
                            <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.4} />
                                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#222" />
                        <XAxis
                            dataKey="time"
                            axisLine={false}
                            tickLine={false}
                            tick={{ fill: '#52525b', fontSize: 12 }}
                            minTickGap={30}
                        />
                        <YAxis
                            axisLine={false}
                            tickLine={false}
                            tick={{ fill: '#52525b', fontSize: 12 }}
                            unit="ms"
                        />
                        <Tooltip
                            contentStyle={{ backgroundColor: '#111', borderColor: '#333', borderRadius: '8px', color: '#fff' }}
                            itemStyle={{ color: '#fff' }}
                            cursor={{ stroke: '#333', strokeWidth: 1 }}
                        />
                        <Area
                            type="monotone"
                            dataKey="latency"
                            stroke="#3b82f6"
                            strokeWidth={3}
                            fillOpacity={1}
                            fill="url(#colorLatency)"
                            animationDuration={500}
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginTop: '24px', paddingTop: '24px', borderTop: '1px solid var(--border-subtle)' }}>
                <div className="stat-mini">
                    <span className="label">Average</span>
                    <span className="value">{avgLatency}ms</span>
                </div>
                <div className="stat-mini">
                    <span className="label">Peak (Low)</span>
                    <span className="value" style={{ color: 'var(--success)' }}>
                        {Math.min(...data.map(d => d.latency))}ms
                    </span>
                </div>
                <div className="stat-mini">
                    <span className="label">Peak (High)</span>
                    <span className="value" style={{ color: 'var(--warning)' }}>
                        {Math.max(...data.map(d => d.latency))}ms
                    </span>
                </div>
            </div>

            <style>{`
        .stat-mini {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .stat-mini .label {
          font-size: 0.75rem;
          color: var(--text-tertiary);
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .stat-mini .value {
          font-size: 1.1rem;
          font-weight: 600;
          color: var(--text-primary);
        }
      `}</style>
        </div>
    );
};

export default SpeedChart;
