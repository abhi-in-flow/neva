import { useMemo } from 'react';
import qrcode from 'qrcode-generator';

// SVG QR of the join URL. Shared by the queued screen's recruiting state and
// (later) the TV leaderboard's recruitment block. White module field per
// brief 06 so venue cameras lock on fast.
export default function JoinQr({ size = 180, label = 'Scan to join the game' }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(window.location.origin);
    qr.make();
    const n = qr.getModuleCount();
    let path = '';
    for (let r = 0; r < n; r += 1) {
      for (let c = 0; c < n; c += 1) {
        if (qr.isDark(r, c)) path += `M${c} ${r}h1v1h-1z`;
      }
    }
    return { path, n };
  }, []);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`-2 -2 ${svg.n + 4} ${svg.n + 4}`}
      role="img"
      aria-label={label}
      style={{ background: '#fff', borderRadius: 12 }}
    >
      <path d={svg.path} fill="#001b1f" />
    </svg>
  );
}
