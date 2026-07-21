import { useState, useEffect, useCallback } from 'react';

/**
 * Custom hook for light/dark theme toggle.
 * Persists preference in localStorage.
 */
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem('arbiter-theme');
    return stored || 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('arbiter-theme', theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, toggle };
}
