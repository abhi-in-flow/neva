import { useEffect, useMemo, useState } from 'react';
import HudBar from '../components/HudBar.jsx';
import '../styles/queued.css';
import '../styles/payoff.css';

// waiting_partner — brief 05: the anticipation half of the payoff. The
// listening motif returns, partner-framed; if the state exposes B's
// attempts_left the dots render live suspense, else copy rotates.
const ROTATE_MS = 4000;

export default function Waiting({ state }) {
  const partner = state.pair?.partner_nickname || 'your partner';
  const lines = useMemo(
    () => [
      `${partner} is listening to you right now…`,
      'Will they get it? Hold tight…',
      `Your words are doing the work, ${state.player?.nickname || 'champ'}.`,
      'The whole round hangs on your voice…',
    ],
    [partner, state.player?.nickname],
  );
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % lines.length), ROTATE_MS);
    return () => clearInterval(t);
  }, [lines]);

  const attempts = state.turn?.attempts_left;

  return (
    <main className="waiting-shell">
      <HudBar player={state.player} />
      <section className="waiting-center" aria-live="polite">
        <div className="pulse-wave" aria-hidden="true">
          <span /><span /><span /><span /><span />
        </div>
        <h1 className="waiting-title">
          Your voice is with <em>{partner}</em>
        </h1>
        {typeof attempts === 'number' ? (
          <p className="waiting-line">
            {attempts === 2 ? 'They’re thinking…' : 'Hmm, first guess missed…'}{' '}
            <span className="attempt-dots" aria-label={`${attempts} tries left`}>
              <i className={attempts >= 1 ? 'on' : ''} />
              <i className={attempts >= 2 ? 'on' : ''} />
            </span>
          </p>
        ) : (
          <p className="waiting-line" key={idx}>{lines[idx]}</p>
        )}
      </section>
    </main>
  );
}
