const ArrowIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </svg>
);

const CheckIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="m5 12 4 4L19 6" />
  </svg>
);

export default function LandingPage({ onOpenDashboard, theme, onToggleTheme }) {
  return (
    <main className="landing">
      <nav className="landing-nav">
        <a className="landing-brand" href="#top" aria-label="Arbiter home">ARBITER</a>
        <div className="landing-nav-actions">
          <button className="landing-link" onClick={onOpenDashboard}>Dashboard</button>
          <button className="theme-toggle" onClick={onToggleTheme} aria-label="Toggle theme">
            {theme === 'dark' ? '☀' : '◐'}
          </button>
        </div>
      </nav>

      <section className="landing-hero" id="top">
        <p className="landing-kicker"><span /> Causal runtime verification</p>
        <h1>Confidence for every<br /><em>agent decision.</em></h1>
        <p className="landing-lede">Arbiter makes the invisible rules of multi-agent systems observable — so your runtime stays safe, explainable, and in control.</p>
        <div className="landing-cta-row">
          <button className="landing-cta" onClick={onOpenDashboard}>Open live dashboard <ArrowIcon /></button>
          <span className="landing-cta-note">No setup required</span>
        </div>
      </section>

      <section className="landing-preview" aria-label="Arbiter capabilities">
        <div className="preview-topline">
          <span>ARBITER / RUNTIME</span>
          <span className="preview-live"><i /> System online</span>
        </div>
        <div className="preview-grid">
          <article className="preview-card preview-card--wide">
            <p className="preview-label">Causal trace</p>
            <div className="trace-lines"><i /><i /><i /><i /></div>
            <p className="preview-value">Every decision, in order.</p>
          </article>
          <article className="preview-card">
            <p className="preview-label">Policy state</p>
            <p className="preview-number">24<span> / 24</span></p>
            <p className="preview-detail"><CheckIcon /> Rules verified</p>
          </article>
          <article className="preview-card">
            <p className="preview-label">Dependency risk</p>
            <div className="mini-graph"><b /><b /><b /><b /></div>
            <p className="preview-detail">Relationships mapped</p>
          </article>
        </div>
      </section>

      <section className="landing-principles">
        <p className="eyebrow">Built for trustworthy systems</p>
        <div>
          <h2>Less guesswork.<br />More governance.</h2>
          <p>Observe ownership, intent, and execution as they happen. Arbiter gives your team a single calm surface for understanding complex agent behaviour.</p>
        </div>
      </section>

      <footer className="landing-footer">
        <span>ARBITER</span><span>Runtime safety for multi-agent systems</span>
      </footer>
    </main>
  );
}
