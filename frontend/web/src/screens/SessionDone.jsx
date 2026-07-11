import Leaderboard from '../components/Leaderboard.jsx';
import { langByCode } from '../lib/languages.js';
import '../styles/payoff.css';
import '../styles/queued.css';

// session_done — brief 05: the verdict stage at session scale. Celebrate
// completion; no "play again" tease (caps are server-enforced).
export default function SessionDone({ state }) {
  const p = state.player || {};
  const native = langByCode(localStorage.getItem('ddf_native_lang'))?.en || 'your language';

  return (
    <main className="done-shell">
      <section className="done-hero">
        <p className="done-kicker">That’s your {p.rounds_cap ?? 20} rounds,</p>
        <h1 className="done-name">{p.nickname}!</h1>
        <div className="done-stats">
          <span className="done-score">{p.score}</span>
          <span className="done-rank">#{p.rank} on the board</span>
        </div>
        <p className="done-pride">
          Your voice just taught an AI <strong>{p.rounds_played}</strong> rounds of{' '}
          <strong>{native}</strong>. That didn’t exist this morning.
        </p>
      </section>

      <section className="board-section">
        <h2 className="board-title">Watch the big screen — the board’s still moving</h2>
        <Leaderboard top={state.leaderboard_top} player={p} />
      </section>
    </main>
  );
}
