import { useEffect, useMemo, useRef, useState } from 'react';
import HudBar from '../components/HudBar.jsx';
import { api } from '../lib/api.js';
import '../styles/guesser.css';

// Guesser screen, brief 04. The listen gate protects the corpus: tiles do
// not exist until the partner's clip has played through once. The 2s poll
// latency after a guess IS the lock-in suspense beat. The client never
// decides correctness — it only diffs attempts_left between polls.

const PlayGlyph = ({ size = 40 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M8.5 5.6v12.8c0 .95 1.04 1.53 1.85 1.03l10.06-6.4a1.22 1.22 0 0 0 0-2.06L10.35 4.57C9.54 4.07 8.5 4.65 8.5 5.6z" />
  </svg>
);

const TILE_COLORS = ['tile-rani', 'tile-sky', 'tile-leaf', 'tile-terra', 'tile-rani', 'tile-sky'];

export default function Guesser({ state }) {
  const turn = state.turn || {};
  const [listened, setListened] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [audioError, setAudioError] = useState(false);
  const [locked, setLocked] = useState(null); // option_id awaiting verdict
  const [dead, setDead] = useState(() => new Set()); // wrong picks this turn
  const audioRef = useRef(null);
  const prevAttempts = useRef(turn.attempts_left);

  // verdict arrives via the poll: still guessing + fewer attempts = wrong pick
  useEffect(() => {
    const now = turn.attempts_left;
    if (locked != null && now < prevAttempts.current) {
      setDead((d) => new Set(d).add(locked));
      setLocked(null);
      navigator.vibrate?.([60, 40, 60]);
    }
    prevAttempts.current = now;
  }, [turn.attempts_left, locked]);

  const play = () => {
    setAudioError(false);
    if (!audioRef.current) {
      const a = new Audio(turn.audio_url);
      a.ontimeupdate = () => a.duration && setProgress(a.currentTime / a.duration);
      a.onended = () => {
        setPlaying(false);
        setProgress(1);
        setListened(true); // the gate opens
      };
      a.onerror = () => {
        setPlaying(false);
        setAudioError(true);
      };
      audioRef.current = a;
    }
    audioRef.current.currentTime = 0;
    audioRef.current.play().catch(() => setAudioError(true));
    setPlaying(true);
  };
  useEffect(() => () => audioRef.current?.pause(), []);

  const pick = async (id) => {
    if (locked != null || dead.has(id)) return;
    navigator.vibrate?.(15);
    setLocked(id);
    try {
      await api.guess(id);
      // outcome arrives via /api/state — the wait is the suspense
    } catch {
      setLocked(null);
    }
  };

  // Indic scripts decide the grid: any long label collapses to single column
  const options = turn.options || [];
  const singleCol = useMemo(() => options.some((o) => o.text.length > 14), [options]);
  const attempts = turn.attempts_left ?? 2;

  return (
    <main className="guesser-shell">
      <HudBar player={state.player} />

      <section className={`listen ${listened ? 'listen-compact' : ''}`}>
        <p className="listen-prompt">
          <strong>{state.pair?.partner_nickname}</strong> describes…
        </p>

        <div className="listen-hero">
          <svg className="ring" viewBox="0 0 120 120" aria-hidden="true">
            <circle className="ring-track" cx="60" cy="60" r="54" />
            <circle
              className="ring-fill"
              cx="60" cy="60" r="54"
              style={{ strokeDashoffset: 339.3 * (1 - progress) }}
            />
          </svg>
          <button
            type="button"
            className="play"
            onClick={play}
            disabled={playing}
            aria-label={listened ? 'Play it again' : 'Play your partner’s clip'}
          >
            <PlayGlyph size={listened ? 26 : 40} />
          </button>
        </div>

        {audioError ? (
          <button type="button" className="audio-retry" onClick={play}>
            Couldn’t fetch their voice — tap to retry
          </button>
        ) : (
          <p className="listen-hint">
            {playing ? 'Listen close…' : listened ? 'Replay anytime' : 'Listen first — then you guess'}
          </p>
        )}
      </section>

      {listened && (
        <section className="answers">
          <div className="answers-head">
            <p className="answers-ask">What did they describe?</p>
            <span className="attempt-dots" aria-label={`${attempts} tries left`}>
              <i className={attempts >= 1 ? 'on' : ''} />
              <i className={attempts >= 2 ? 'on' : ''} />
            </span>
          </div>

          <div className={`tile-grid ${singleCol ? 'tile-grid-rows' : ''}`}>
            {options.map((o, i) => {
              const isDead = dead.has(o.id);
              const isLocked = locked === o.id;
              return (
                <button
                  key={o.id}
                  type="button"
                  lang="und"
                  className={`tile ${TILE_COLORS[i]} ${isDead ? 'tile-dead' : ''} ${isLocked ? 'tile-locked' : ''}`}
                  style={{ '--i': i }}
                  disabled={isDead || locked != null}
                  onClick={() => pick(o.id)}
                >
                  {o.text}
                  {isLocked && <span className="tile-lockmsg">locked in…</span>}
                </button>
              );
            })}
          </div>
        </section>
      )}
    </main>
  );
}
