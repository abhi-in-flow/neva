import { useEffect, useRef, useState } from 'react';
import JoinQr from '../components/JoinQr.jsx';
import {
  TV_BEAT_MS,
  TV_LEADERBOARD_POLL_MS,
  TV_METRICS_POLL_MS,
  TV_SHOW_ROWS,
  TV_TICKER_ROTATE_MS,
} from '../lib/constants.js';
import { buildMetricsTickerLines } from '../lib/metricsTicker.js';
import '../styles/tv.css';

// /tv — brief 06: a QR-dominant recruitment poster backed by a live top-8
// board. Unauthenticated, 16:9 landscape, runs all day. Consumes the frozen
// LeaderboardResponse ({ entries }) and MetricsResponse fields from
// contracts/api_types.py — no legacy { top }, clips, or numeric languages.

const PLAYFUL = [
  'Every validated pair on this board is a real voice from this room',
  'Your grandmother’s word for it wins points here',
  'No two players sound the same — that’s the point',
];

const Crown = () => (
  <svg viewBox="0 0 24 24" className="crown" aria-hidden="true">
    <path d="M3 8l4.5 4L12 5l4.5 7L21 8l-1.6 10H4.6L3 8z" fill="currentColor" />
  </svg>
);

export default function Tv() {
  const [board, setBoard] = useState(null); // null = first load
  const [metrics, setMetrics] = useState(null);
  const [stale, setStale] = useState(false);
  const [beat, setBeat] = useState(null); // { kind, text }
  const [tick, setTick] = useState(0);
  const prevBoard = useRef(new Map());
  const beatQueue = useRef([]);
  const beatBusy = useRef(false);
  const listRef = useRef(null);
  const rowTops = useRef(new Map());

  const playBeats = () => {
    if (beatBusy.current) return;
    const next = beatQueue.current.shift();
    if (!next) return;
    beatBusy.current = true;
    setBeat(next);
    setTimeout(() => {
      setBeat(null);
      beatBusy.current = false;
      playBeats();
    }, TV_BEAT_MS);
  };

  // board poll + diff → beats
  useEffect(() => {
    let alive = true;
    let timer;
    const poll = async () => {
      try {
        const res = await fetch('/api/leaderboard?top=15');
        if (!res.ok) throw new Error();
        const { entries } = await res.json();
        const rows = Array.isArray(entries) ? entries : [];
        if (!alive) return;
        setStale(false);

        const prev = prevBoard.current;
        if (prev.size > 0) {
          const events = [];
          const prevFirst = [...prev.entries()].sort((a, b) => b[1].score - a[1].score)[0]?.[0];
          rows.slice(0, TV_SHOW_ROWS).forEach((row, i) => {
            const was = prev.get(row.nickname);
            if (!was) events.push({ kind: 'welcome', text: `Welcome, ${row.nickname}! 👋`, row: row.nickname });
            else if (row.score > was.score)
              events.push({ kind: 'score', text: `${row.nickname} +${row.score - was.score}`, row: row.nickname });
            if (i === 0 && prevFirst && prevFirst !== row.nickname)
              events.push({ kind: 'crown', text: `${row.nickname} takes the crown! 👑`, row: row.nickname });
          });
          beatQueue.current.push(...events.slice(0, 3));
          playBeats();
        }
        prevBoard.current = new Map(rows.map((r) => [r.nickname, r]));
        setBoard(rows);
      } catch {
        if (alive) setStale(true); // freeze gracefully; the QR never looks broken
      }
      timer = setTimeout(poll, TV_LEADERBOARD_POLL_MS);
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  // metrics poll for the ticker
  useEffect(() => {
    let alive = true;
    let timer;
    const poll = async () => {
      try {
        const res = await fetch('/api/metrics');
        if (res.ok && alive) setMetrics(await res.json());
      } catch { /* ticker just keeps its last numbers */ }
      timer = setTimeout(poll, TV_METRICS_POLL_MS);
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, []);

  // rotate the ticker line
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), TV_TICKER_ROTATE_MS);
    return () => clearInterval(t);
  }, []);

  // FLIP re-rank on the 4–8 list (long-runtime safe: Web Animations, no accumulation)
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const prev = rowTops.current;
    const next = new Map();
    for (const row of list.children) {
      const key = row.dataset.key;
      const top = row.getBoundingClientRect().top;
      next.set(key, top);
      const was = prev.get(key);
      if (was !== undefined && was !== top) {
        row.animate(
          [{ transform: `translateY(${was - top}px)` }, { transform: 'translateY(0)' }],
          { duration: 500, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
        );
      }
    }
    rowTops.current = next;
  }, [board]);

  const tickerLines = [...buildMetricsTickerLines(metrics), ...PLAYFUL].filter(Boolean);
  const tickerLine = tickerLines[tick % tickerLines.length] || '';

  const podium = board?.slice(0, 3) || [];
  const rest = board?.slice(3, TV_SHOW_ROWS) || [];
  const empty = board && board.length === 0;

  return (
    <main className="tv">
      <section className="tv-board" aria-label="Live leaderboard">
        {empty || !board ? (
          <div className="tv-empty">
            <h2 className="tv-empty-line">The board is empty.</h2>
            <p className="tv-empty-sub">Be the first. →</p>
          </div>
        ) : (
          <>
            <div className="podium">
              {[podium[1], podium[0], podium[2]].map(
                (row, i) =>
                  row && (
                    <div
                      key={row.nickname}
                      className={`podium-slot podium-${i === 1 ? 1 : i === 0 ? 2 : 3} ${
                        beat?.row === row.nickname ? 'row-beat' : ''
                      }`}
                    >
                      {i === 1 && <Crown />}
                      <span className="podium-name">{row.nickname}</span>
                      <span className="podium-score">{row.score}</span>
                    </div>
                  ),
              )}
            </div>
            <ol className="tv-list" ref={listRef}>
              {rest.map((row, i) => (
                <li key={row.nickname} data-key={row.nickname} className={beat?.row === row.nickname ? 'row-beat' : ''}>
                  <span className="tv-rank">{i + 4}</span>
                  <span className="tv-name">{row.nickname}</span>
                  <span className="tv-score">{row.score}</span>
                </li>
              ))}
            </ol>
          </>
        )}
        {beat && (
          <div className={`beat-banner beat-${beat.kind}`} role="status">
            {beat.text}
          </div>
        )}
        {stale && <span className="tv-stale" aria-label="Reconnecting">⟳</span>}
      </section>

      <aside className="tv-recruit">
        <h1 className="tv-pitch">
          Scan.<br />Speak.<br />Score.
        </h1>
        <div className="tv-qr">
          <JoinQr size={400} label="Scan to join the game" />
        </div>
        <p className="tv-pitch-sub">Your language wins games here.</p>
      </aside>

      <footer className="tv-ticker" aria-live="off">
        <span key={tick} className="tv-ticker-line">{tickerLine}</span>
      </footer>
    </main>
  );
}
