import { useArbiterSocket } from './hooks/useArbiterSocket';
import { useTheme } from './hooks/useTheme';
import Header from './components/Header';
import EventFeed from './components/EventFeed';
import StateMachines from './components/StateMachines';
import DependencyGraph from './components/DependencyGraph';
import Violations from './components/Violations';

export default function App() {
  const { connected, events, states, graph, violations, agents, clearEvents } =
    useArbiterSocket();
  const { theme, toggle } = useTheme();

  return (
    <>
      <Header
        connected={connected}
        eventCount={events.length}
        agentCount={agents.size}
        violationCount={violations.length}
        theme={theme}
        onToggleTheme={toggle}
      />
      <main className="dashboard">
        <EventFeed events={events} onClear={clearEvents} />
        <StateMachines states={states} />
        <DependencyGraph graph={graph} theme={theme} />
        <Violations violations={violations} />
      </main>
    </>
  );
}
