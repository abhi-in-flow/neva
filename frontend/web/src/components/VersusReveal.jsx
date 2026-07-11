import { useEffect, useState } from 'react';
import { langByCode } from '../lib/languages.js';
import '../styles/versus.css';

// Match-found beat (brief 02): pure client theater over the queued → turn
// phase transition. Auto-dismisses after 1.5s; any tap skips; the next
// screen is already live underneath — this never blocks input for long.
const BEAT_MS = 1500;

export default function VersusReveal({ pair, you, onDone }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setLeaving(true), BEAT_MS);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!leaving) return undefined;
    const t = setTimeout(onDone, 240);
    return () => clearTimeout(t);
  }, [leaving, onDone]);

  const lang = langByCode(pair?.common_lang)?.en || pair?.common_lang;

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className={`versus ${leaving ? 'versus-out' : ''}`}
      onPointerDown={() => setLeaving(true)}
      role="status"
      aria-label={`Matched with ${pair?.partner_nickname}`}
    >
      <p className="versus-you">{you}</p>
      <p className="versus-vs" aria-hidden="true">vs</p>
      <p className="versus-them">{pair?.partner_nickname}</p>
      {lang && <p className="versus-lang">You both speak {lang}!</p>}
    </div>
  );
}
