import { useEffect, useRef, useState } from 'react';
import { getToken } from './lib/api.js';
import { usePoll } from './lib/usePoll.js';
import { usePairRequestWhileWaiting } from './lib/usePairRequestWhileWaiting.js';
import Join from './screens/Join.jsx';
import Queued from './screens/Queued.jsx';
import Speaker from './screens/Speaker.jsx';
import Guesser from './screens/Guesser.jsx';
import Waiting from './screens/Waiting.jsx';
import Result from './screens/Result.jsx';
import SessionDone from './screens/SessionDone.jsx';
import VersusReveal from './components/VersusReveal.jsx';

// The phase field IS the router (work order §2.1): one component per phase,
// a top-level switch, nothing else. Fields irrelevant to the current phase
// are null and are never cached across phases.

function PhaseStub({ state }) {
  return (
    <main style={{ display: 'grid', placeItems: 'center', flex: 1, padding: 24, textAlign: 'center' }}>
      <div>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--text-title)' }}>
          {state.phase}
        </h1>
        <p style={{ color: 'var(--muted)', marginTop: 12 }}>
          This screen ships in its own craft pass.
        </p>
      </div>
    </main>
  );
}

const PHASES = {
  onboarding: Queued,
  queued: Queued,
  speaking_view_image: Speaker,
  speaking_confirm_label: Speaker,
  guessing: Guesser,
  waiting_partner: Waiting,
  round_result: Result,
  session_done: SessionDone,
};

const TURN_PHASES = ['speaking_view_image', 'guessing'];

export default function App() {
  const [joined, setJoined] = useState(() => Boolean(getToken()));
  const { state, reconnecting } = usePoll(joined);
  usePairRequestWhileWaiting(state?.phase);
  const [versus, setVersus] = useState(null); // { pair, you }
  const prevPhase = useRef(null);
  const lastRole = useRef(null); // remembered so round_result can flavor its copy

  useEffect(() => {
    const onUnauthorized = () => setJoined(false);
    window.addEventListener('ddf:unauthorized', onUnauthorized);
    return () => window.removeEventListener('ddf:unauthorized', onUnauthorized);
  }, []);

  // Match-found beat: theater layered over the queued → turn transition,
  // never blocking it — the round screen renders underneath immediately.
  useEffect(() => {
    if (!state) return;
    if (
      prevPhase.current === 'queued' &&
      TURN_PHASES.includes(state.phase) &&
      state.pair
    ) {
      setVersus({ pair: state.pair, you: state.player?.nickname || 'You' });
    }
    if (state.turn?.role) lastRole.current = state.turn.role;
    prevPhase.current = state.phase;
  }, [state]);

  if (!joined) return <Join onJoined={() => setJoined(true)} />;

  const Screen = state ? PHASES[state.phase] || PhaseStub : Queued;

  return (
    <>
      <Screen state={state || {}} lastRole={lastRole.current} />
      {versus && (
        <VersusReveal
          pair={versus.pair}
          you={versus.you}
          onDone={() => setVersus(null)}
        />
      )}
      {reconnecting && (
        <div className="reconnect-pill" role="status">
          reconnecting…
        </div>
      )}
    </>
  );
}
