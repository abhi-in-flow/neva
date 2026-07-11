import { useEffect, useMemo, useState } from 'react';
import Leaderboard from '../components/Leaderboard.jsx';
import JoinQr from '../components/JoinQr.jsx';
import { langByCode } from '../lib/languages.js';
import '../styles/queued.css';

// Brief 02: searching hero + leaderboard below; ~20s escalation into
// recruiting mode with the join QR. No warm CTA exists here — the accent
// is saved for the match-found beat (The One Action Rule's zero case).

const COPY_ROTATE_MS = 4000;
// ?recruitAfter=<ms> lets tests and demos reach recruiting mode quickly.
const RECRUIT_AFTER_MS =
  Number(new URLSearchParams(window.location.search).get('recruitAfter')) || 20000;

function searchingLines() {
  const nativeCode = localStorage.getItem('ddf_native_lang');
  const native = nativeCode ? langByCode(nativeCode)?.en : null;
  return [
    'Looking for a different mother tongue…',
    native
      ? `Finding someone who speaks a language other than ${native}…`
      : 'Matching different native languages across the hall…',
    'Checking for a shared language you both know…',
    'Matchmaking continues automatically while you wait…',
    'Warming up the scoreboard…',
    'Listening for new players…',
  ];
}

export default function Queued({ state }) {
  const lines = useMemo(searchingLines, []);
  const [lineIdx, setLineIdx] = useState(0);
  const [recruiting, setRecruiting] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setLineIdx((i) => (i + 1) % lines.length), COPY_ROTATE_MS);
    return () => clearInterval(t);
  }, [lines]);

  useEffect(() => {
    const t = setTimeout(() => setRecruiting(true), RECRUIT_AFTER_MS);
    return () => clearTimeout(t);
  }, []);

  return (
    <main className="queued-shell">
      <section className={`search-hero ${recruiting ? 'search-hero-recruit' : ''}`} aria-live="polite">
        {!recruiting ? (
          <>
            <div className="pulse-wave" aria-hidden="true">
              <span /><span /><span /><span /><span />
            </div>
            <h1 className="search-title">Finding your partner</h1>
            <p className="search-line" key={lineIdx}>
              {lines[lineIdx]}
            </p>
          </>
        ) : (
          <>
            <h1 className="search-title">Invite another player</h1>
            <p className="search-line">
              Share this onboarding QR. We’ll match automatically when someone
              with a different mother tongue shares another language with you. 👋
            </p>
            <div className="recruit-qr">
              <JoinQr size={168} />
            </div>
          </>
        )}
      </section>

      <section className="board-section">
        <h2 className="board-title">Meanwhile, on the big screen</h2>
        <Leaderboard top={state?.leaderboard_top} player={state?.player} />
      </section>
    </main>
  );
}
