import { useLayoutEffect, useRef } from 'react';

// Ranked list, no cards (DESIGN.md ban). Rows FLIP to their new position
// between polls — visibly alive, never flashing.
export default function Leaderboard({ top, player }) {
  const listRef = useRef(null);
  const positions = useRef(new Map());

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const prev = positions.current;
    const next = new Map();
    for (const row of list.children) {
      const key = row.dataset.key;
      const box = row.getBoundingClientRect();
      next.set(key, box.top);
      const before = prev.get(key);
      if (before !== undefined && before !== box.top) {
        const dy = before - box.top;
        row.animate(
          [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
          { duration: 420, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
        );
      }
    }
    positions.current = next;
  });

  if (!top || top.length === 0) {
    return (
      <p className="board-empty">
        The board is empty. Be the first name on that TV.
      </p>
    );
  }

  const inTop = player && top.some((r) => r.nickname === player.nickname);

  return (
    <ol className="board" ref={listRef} aria-label="Leaderboard">
      {top.map((row, i) => {
        const you = player && row.nickname === player.nickname;
        return (
          <li key={row.nickname} data-key={row.nickname} className={you ? 'board-you' : ''}>
            <span className="board-rank">{i + 1}</span>
            <span className="board-name">{row.nickname}</span>
            <span className="board-score">{row.score}</span>
          </li>
        );
      })}
      {player && !inTop && (
        <li data-key="__you" className="board-you board-pinned">
          <span className="board-rank">{player.rank}</span>
          <span className="board-name">{player.nickname}</span>
          <span className="board-score">{player.score}</span>
        </li>
      )}
    </ol>
  );
}
