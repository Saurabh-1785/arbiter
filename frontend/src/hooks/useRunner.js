import { useState, useCallback } from 'react';

/**
 * Custom hook for triggering demo runs and test suites via the API.
 */
export function useRunner() {
  const [demoStatus, setDemoStatus] = useState('idle'); // idle | running | completed | error
  const [demoResults, setDemoResults] = useState(null);
  const [testStatus, setTestStatus] = useState('idle');
  const [testResults, setTestResults] = useState(null);

  const runDemo = useCallback(async () => {
    setDemoStatus('running');
    setDemoResults(null);

    try {
      const res = await fetch('/api/run-demo', { method: 'POST' });
      const data = await res.json();
      setDemoResults(data);
      setDemoStatus(data.status === 'completed' ? 'completed' : 'error');
    } catch (err) {
      setDemoResults({ status: 'error', message: err.message });
      setDemoStatus('error');
    }
  }, []);

  const runTests = useCallback(async () => {
    setTestStatus('running');
    setTestResults(null);

    try {
      const res = await fetch('/api/run-tests', { method: 'POST' });
      const data = await res.json();
      setTestResults(data);
      setTestStatus(data.status === 'completed' ? 'completed' : 'error');
    } catch (err) {
      setTestResults({ status: 'error', message: err.message });
      setTestStatus('error');
    }
  }, []);

  const clearResults = useCallback(() => {
    setDemoStatus('idle');
    setDemoResults(null);
    setTestStatus('idle');
    setTestResults(null);
  }, []);

  return {
    demoStatus,
    demoResults,
    testStatus,
    testResults,
    runDemo,
    runTests,
    clearResults,
  };
}
