import { useState } from 'react';
import { useArbiterSocket } from './hooks/useArbiterSocket';
import { useTheme } from './hooks/useTheme';
import { useRunner } from './hooks/useRunner';
import Header from './components/Header';
import ResultsPanel from './components/ResultsPanel';
import EventFeed from './components/EventFeed';
import StateMachines from './components/StateMachines';
import DependencyGraph from './components/DependencyGraph';
import Violations from './components/Violations';
import LandingPage from './components/LandingPage';

export default function App() {
  const [showDashboard, setShowDashboard] = useState(() => window.location.hash === '#dashboard');
  const { connected, events, states, graph, violations, agents, clearEvents } =
    useArbiterSocket();
  const { theme, toggle } = useTheme();
  const {
    demoStatus, demoResults, testStatus, testResults,
    runDemo, runTests, clearResults,
  } = useRunner();

  const openDashboard = () => {
    window.location.hash = 'dashboard';
    setShowDashboard(true);
  };

  if (!showDashboard) {
    return <LandingPage theme={theme} onToggleTheme={toggle} onOpenDashboard={openDashboard} />;
  }

  return (
    <>
      <Header
        connected={connected}
        eventCount={events.length}
        agentCount={agents.size}
        violationCount={violations.length}
        theme={theme}
        onToggleTheme={toggle}
        onRunDemo={runDemo}
        onRunTests={runTests}
        demoStatus={demoStatus}
        testStatus={testStatus}
      />
      <ResultsPanel
        demoStatus={demoStatus}
        demoResults={demoResults}
        testStatus={testStatus}
        testResults={testResults}
        onClose={clearResults}
      />
      <section className="workspace-intro">
        <div>
          <p className="eyebrow">Runtime observability</p>
          <h2>Trust the sequence, not just the outcome.</h2>
          <p className="workspace-copy">Monitor causal decisions, ownership state, and dependency risk across your agent runtime.</p>
        </div>
        <div className="workspace-status">
          <span className={`status-indicator ${connected ? 'is-live' : ''}`} />
          <span>{connected ? 'Verifier stream is connected' : 'Waiting for verifier stream'}</span>
        </div>
      </section>
      <main className="dashboard">
        <EventFeed events={events} onClear={clearEvents} />
        <StateMachines states={states} />
        <DependencyGraph graph={graph} theme={theme} />
        <Violations violations={violations} />
      </main>
    </>
  );
}
