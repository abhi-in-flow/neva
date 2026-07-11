import { useEffect, useState } from 'react';
import HudBar from '../components/HudBar.jsx';
import Confetti from '../components/Confetti.jsx';
import '../styles/payoff.css';

// round_result — brief 05: the shared verdict stage, role-flavored copy.
// validated = the accent floods (rhyming with the versus reveal) + confetti
// + points fly-up. wrong/unclear stay in the peacock world, warm and quiet.
// The 2.5s draining ring collapses the takeover; tap skips; the server
// drives the actual phase advance.
const BEAT_MS = 2500;

const HEADLINES = {
  validated: 'VALIDATED!',
  wrong: 'So close',
  unclear: 'Too tricky',
};

function roleLine(outcome, role, partner) {
  if (outcome === 'validated') {
    return role === 'speaker' ? `${partner} understood you!` : 'You got it!';
  }
  if (outcome === 'wrong') {
    return role === 'guesser' ? 'Not this time — good ear anyway.' : `${partner} couldn’t place it.`;
  }
  return 'No points this round — no harm done.';
}

export default function Result({ state, lastRole }) {
  const result = state.last_result || { outcome: 'unclear', points_delta: 0 };
  const { outcome } = result;
  const validated = outcome === 'validated';
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setCollapsed(true), BEAT_MS);
    return () => clearTimeout(t);
  }, []);

  if (collapsed) {
    return (
      <main className="waiting-shell">
        <HudBar player={state.player} />
        <section className="waiting-center">
          <p className="waiting-line">Next round coming up…</p>
        </section>
      </main>
    );
  }

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className={`verdict ${validated ? 'verdict-validated' : 'verdict-quiet'}`}
      onPointerDown={() => setCollapsed(true)}
      role="status"
      aria-label={`Round result: ${outcome}`}
    >
      {validated && <Confetti />}
      <div className="verdict-stage">
        <svg className="drain" viewBox="0 0 44 44" aria-hidden="true">
          <circle cx="22" cy="22" r="19" />
        </svg>
        <h1 className="verdict-word">{HEADLINES[outcome] || outcome}</h1>
        {validated && result.points_delta > 0 && (
          <p className="verdict-points" aria-hidden="true">+{result.points_delta}</p>
        )}
        <p className="verdict-role">
          {roleLine(outcome, lastRole, state.pair?.partner_nickname || 'Your partner')}
        </p>
        {result.message && <p className="verdict-msg">{result.message}</p>}
      </div>
    </div>
  );
}
