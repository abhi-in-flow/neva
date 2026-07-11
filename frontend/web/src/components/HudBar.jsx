// Persistent score header shared across game screens (brief 05 §7):
// renders server truth only — fly-up animations elsewhere are presentation.
export default function HudBar({ player }) {
  if (!player) return null;
  return (
    <header className="hud">
      <span className="hud-name">{player.nickname}</span>
      <span className="hud-rounds">
        {player.rounds_played}/{player.rounds_cap}
      </span>
      <span className="hud-score" aria-label={`Score ${player.score}`}>
        {player.score}
      </span>
    </header>
  );
}
