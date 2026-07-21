const SunIcon = () => (
  <svg className="icon-sun" width="18" height="18" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="5" />
    <line x1="12" y1="1" x2="12" y2="3" />
    <line x1="12" y1="21" x2="12" y2="23" />
    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
    <line x1="1" y1="12" x2="3" y2="12" />
    <line x1="21" y1="12" x2="23" y2="12" />
    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
  </svg>
);

const MoonIcon = () => (
  <svg className="icon-moon" width="18" height="18" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  </svg>
);

export default function Header({
  connected,
  eventCount,
  agentCount,
  violationCount,
  theme,
  onToggleTheme,
}) {
  return (
    <header className="header">
      <div className="header-left">
        <h1 className="logo">ARBITER</h1>
        <span className="divider" />
        <span className="tagline">Causal Runtime Verifier</span>
      </div>

      <div className="header-right">
        <div className="metrics">
          <div className="metric">
            <span className="metric-val">{eventCount}</span>
            <span className="metric-label">Events</span>
          </div>
          <div className="metric">
            <span className="metric-val">{agentCount}</span>
            <span className="metric-label">Agents</span>
          </div>
          <div className={`metric ${violationCount > 0 ? 'metric--danger' : ''}`}>
            <span className="metric-val">{violationCount}</span>
            <span className="metric-label">Violations</span>
          </div>
        </div>

        <div className="header-actions">
          <div className="connection">
            <span className={`conn-dot ${connected ? 'connected' : ''}`} />
            <span className="conn-text">{connected ? 'Live' : 'Offline'}</span>
          </div>
          <button className="theme-toggle" onClick={onToggleTheme} aria-label="Toggle theme">
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </div>
    </header>
  );
}
