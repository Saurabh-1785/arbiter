import { useState, useEffect, useRef, useCallback } from 'react';

/**
 * Custom hook for ARBITER WebSocket connection.
 *
 * Connects to /ws, parses typed messages (event, state_update, graph_update,
 * violation), and returns reactive state for all four dashboard panels.
 */
export function useArbiterSocket() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState([]);
  const [states, setStates] = useState({});
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [violations, setViolations] = useState([]);
  const [agents, setAgents] = useState(new Set());

  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      console.log('[ARBITER] Connected');
    };

    ws.onclose = () => {
      setConnected(false);
      console.log('[ARBITER] Disconnected — reconnecting in 2s');
      reconnectTimer.current = setTimeout(connect, 2000);
    };

    ws.onerror = (err) => {
      console.error('[ARBITER] WS error:', err);
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        dispatch(msg);
      } catch (e) {
        console.error('[ARBITER] Parse error:', e);
      }
    };
  }, []);

  const dispatch = useCallback((msg) => {
    switch (msg.type) {
      case 'event':
        setEvents((prev) => [...prev, msg.data]);
        setAgents((prev) => new Set(prev).add(msg.data.agent_id));
        break;
      case 'state_update':
        setStates((prev) => ({ ...prev, [msg.data.resource_id]: msg.data }));
        break;
      case 'graph_update':
        setGraph(msg.data);
        break;
      case 'violation':
        setViolations((prev) => [...prev, msg.data]);
        break;
      default:
        break;
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  return {
    connected,
    events,
    states,
    graph,
    violations,
    agents,
    clearEvents,
  };
}
