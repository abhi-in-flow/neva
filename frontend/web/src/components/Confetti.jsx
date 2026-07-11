import { useEffect, useRef } from 'react';

// Bounded canvas confetti burst (~1.5s), brand palette, never loops,
// never blocks input (pointer-events: none), skipped under reduced motion.
const COLORS = ['#f5ae39', '#d53a84', '#3d89ad', '#3d8a5c', '#ecf6f5'];
const LIFE_MS = 1500;

export default function Confetti() {
  const ref = useRef(null);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
    const canvas = ref.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    const g = canvas.getContext('2d');
    g.scale(dpr, dpr);
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;

    const parts = Array.from({ length: 90 }, () => {
      const angle = -Math.PI / 2 + (Math.random() - 0.5) * 1.6;
      const speed = 7 + Math.random() * 9;
      return {
        x: W / 2, y: H * 0.62,
        vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        w: 5 + Math.random() * 6, h: 8 + Math.random() * 6,
        rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
        color: COLORS[(Math.random() * COLORS.length) | 0],
      };
    });

    const t0 = performance.now();
    let raf;
    const tick = (t) => {
      const k = (t - t0) / LIFE_MS;
      g.clearRect(0, 0, W, H);
      if (k >= 1) return;
      for (const p of parts) {
        p.vy += 0.32;
        p.x += p.vx;
        p.y += p.vy;
        p.rot += p.vr;
        g.save();
        g.translate(p.x, p.y);
        g.rotate(p.rot);
        g.globalAlpha = 1 - k * k;
        g.fillStyle = p.color;
        g.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        g.restore();
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} className="confetti" aria-hidden="true" />;
}
