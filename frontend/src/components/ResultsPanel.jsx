/**
 * ResultsPanel — Shows demo and test results as a slide-down panel below the header.
 */
export default function ResultsPanel({
  demoStatus,
  demoResults,
  testStatus,
  testResults,
  onClose,
}) {
  const showDemo = demoStatus !== 'idle';
  const showTests = testStatus !== 'idle';

  if (!showDemo && !showTests) return null;

  return (
    <div className="results-panel" id="results-panel">
      <div className="results-inner">
        {showDemo && (
          <DemoResults status={demoStatus} results={demoResults} />
        )}
        {showTests && (
          <TestResults status={testStatus} results={testResults} />
        )}
      </div>
      <button className="results-close" onClick={onClose} aria-label="Close results">
        ✕
      </button>
    </div>
  );
}

function DemoResults({ status, results }) {
  if (status === 'running') {
    return (
      <div className="result-section">
        <div className="result-title">
          <span className="spinner" />
          Running Three-Act Demo…
        </div>
        <p className="result-hint">Events are streaming live to the dashboard panels.</p>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="result-section result-section--error">
        <div className="result-title">Demo Error</div>
        <p className="result-hint">{results?.message || 'Unknown error'}</p>
      </div>
    );
  }

  if (!results || !results.results) return null;

  const { act1, act2, act3 } = results.results;
  const allPassed = results.summary?.all_passed;

  return (
    <div className="result-section">
      <div className="result-title">
        <span className={`result-badge ${allPassed ? 'result-badge--pass' : 'result-badge--fail'}`}>
          {allPassed ? '✓ ALL PASSED' : '✗ FAILED'}
        </span>
        Three-Act Demo
      </div>
      <div className="result-acts">
        <ActCard
          title="Act 1 — Happy Path"
          items={[
            { label: 'State', value: act1.final_state, ok: act1.final_state === 'Acked' },
            { label: 'Violations', value: act1.violations, ok: act1.violations === 0 },
            { label: 'Cycles', value: act1.cycles, ok: act1.cycles === 0 },
          ]}
        />
        <ActCard
          title="Act 2 — Fencing"
          items={[
            { label: 'Stale rejected', value: String(act2.agent_a_write_rejected), ok: act2.agent_a_write_rejected },
            { label: 'Valid accepted', value: String(act2.agent_b_write_accepted), ok: act2.agent_b_write_accepted },
            { label: 'Writes in log', value: act2.writes_in_log, ok: act2.writes_in_log === 1 },
          ]}
        />
        <ActCard
          title="Act 3 — Cycles"
          items={[
            { label: 'Cycles found', value: act3.cycles_detected, ok: act3.cycles_detected >= 1 },
            { label: 'A revoked', value: String(act3.agent_a_tokens_revoked), ok: act3.agent_a_tokens_revoked },
            { label: 'C revoked', value: String(act3.agent_c_tokens_revoked), ok: act3.agent_c_tokens_revoked },
          ]}
        />
      </div>
    </div>
  );
}

function ActCard({ title, items }) {
  return (
    <div className="act-card">
      <div className="act-title">{title}</div>
      {items.map((item, i) => (
        <div key={i} className="act-row">
          <span className="act-label">{item.label}</span>
          <span className={`act-value ${item.ok ? 'act-value--ok' : 'act-value--bad'}`}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}

function TestResults({ status, results }) {
  if (status === 'running') {
    return (
      <div className="result-section">
        <div className="result-title">
          <span className="spinner" />
          Running Test Suite…
        </div>
        <p className="result-hint">This may take a few seconds.</p>
      </div>
    );
  }

  if (status === 'error' || results?.status === 'error') {
    return (
      <div className="result-section result-section--error">
        <div className="result-title">Test Error</div>
        <p className="result-hint">{results?.message || 'Unknown error'}</p>
      </div>
    );
  }

  if (!results) return null;

  return (
    <div className="result-section">
      <div className="result-title">
        <span className={`result-badge ${results.passed ? 'result-badge--pass' : 'result-badge--fail'}`}>
          {results.passed ? '✓ ALL PASSED' : '✗ FAILURES'}
        </span>
        Test Suite — {results.passed_count}/{results.total} passed
      </div>
      <div className="test-grid">
        {results.tests && results.tests.map((t, i) => (
          <div key={i} className={`test-row ${t.status}`}>
            <span className="test-status-dot" />
            <span className="test-name">{formatTestName(t.name)}</span>
          </div>
        ))}
      </div>
      {results.summary && (
        <div className="test-summary">{results.summary}</div>
      )}
    </div>
  );
}

function formatTestName(name) {
  // "tests/test_hlc.py::TestHLCMonotonicity::test_local_events_increase" → "test_local_events_increase"
  const parts = name.split('::');
  return parts.length > 1 ? parts[parts.length - 1] : name.split('/').pop();
}
