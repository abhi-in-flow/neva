import { useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { RECONNECT_FAIL_THRESHOLD, STATE_POLL_MS } from './constants.js';

// The one polling hook (work order §2.1): GET /api/state every 2s.
// state_version skip, silent failure retry, "reconnecting" after 3 misses.

export function usePoll(enabled) {
  const [state, setState] = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  const versionRef = useRef(-1);
  const failsRef = useRef(0);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    let timer;

    async function tick() {
      try {
        const next = await api.state();
        if (!alive) return;
        failsRef.current = 0;
        setReconnecting(false);
        if (next.state_version !== versionRef.current) {
          versionRef.current = next.state_version;
          setState(next);
        }
      } catch (err) {
        if (!alive) return;
        if (err.status === 401) return; // api.js already routed to join
        failsRef.current += 1;
        if (failsRef.current >= RECONNECT_FAIL_THRESHOLD) setReconnecting(true);
      }
      timer = setTimeout(tick, STATE_POLL_MS);
    }

    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [enabled]);

  return { state, reconnecting };
}
