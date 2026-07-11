// Mock backend (work order §2.4): canned /api/state fixtures aligned with
// contracts/api_types.py. Run: node mock/server.mjs (port 8787).
import { createServer } from 'node:http';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** UUID of the correct guess option in mock/fixtures/guessing.json. */
const CORRECT_OPTION_ID = '00000000-0000-4000-8000-000000000002';

const dir = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const fixtures = {};
for (const f of readdirSync(dir)) {
  if (f.endsWith('.json')) fixtures[f.replace('.json', '')] = JSON.parse(readFileSync(join(dir, f)));
}

const CYCLE = [
  'onboarding', 'queued', 'speaking_view_image', 'speaking_confirm_label',
  'waiting_partner', 'guessing', 'round_result', 'session_done',
];
let phaseIdx = 0; // join lands in onboarding; pair/request advances to queued
let version = 1;
let audioAttempts = 0;
let queuedPolls = 0; // auto-match after a few polls so the versus beat is demoable
let guessAttempts = 2;
let lastOutcome = null; // set when a guess resolves the round
let resultPolls = 0;
let boardPolls = 0;
const board = [...fixtures.leaderboard.entries];
const metricsBase = { ...fixtures.metrics };

const send = (res, code, body) => {
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  const { url, method } = req;
  if (method === 'POST' && url === '/api/join') {
    phaseIdx = 0;
    version += 1;
    queuedPolls = 0;
    return send(res, 200, { session_token: 'mock-token-' + Date.now() });
  }
  if (method === 'GET' && url.startsWith('/api/state')) {
    if (CYCLE[phaseIdx] === 'queued') {
      queuedPolls += 1;
      if (queuedPolls > 6) { // ~12s of searching, then a match
        phaseIdx = 2; version += 1; queuedPolls = 0;
      }
    }
    const name = CYCLE[phaseIdx];
    const fixture = fixtures[name] || fixtures.queued;
    const out = { ...fixture, state_version: version };
    if (name === 'guessing') out.turn = { ...fixture.turn, attempts_left: guessAttempts };
    if (name === 'round_result') {
      if (lastOutcome) out.last_result = lastOutcome;
      resultPolls += 1;
      if (resultPolls > 3) { // next round begins
        phaseIdx = 2; resultPolls = 0; lastOutcome = null; version += 1;
      }
    }
    return send(res, 200, out);
  }
  if (method === 'POST' && url === '/api/pair/request') {
    if (phaseIdx === 0) {
      phaseIdx = 1;
      version += 1;
      queuedPolls = 0;
    }
    return send(res, 200, { status: 'queued' });
  }
  if (method === 'POST' && url === '/api/turn/audio') {
    audioAttempts += 1;
    // First upload of each turn asks for a re-record so the shake state is testable.
    if (audioAttempts % 2 === 1) {
      return send(res, 200, { status: 're_record', reason: "Didn't catch that — louder! 🔊" });
    }
    phaseIdx = 3; version += 1;
    return send(res, 200, { status: 'ok' });
  }
  if (method === 'POST' && url === '/api/turn/confirm-label') {
    phaseIdx = 4; version += 1;
    return send(res, 200, {});
  }
  if (method === 'POST' && url === '/api/turn/guess') {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const { option_id } = JSON.parse(body || '{}');
      if (option_id === CORRECT_OPTION_ID) {
        phaseIdx = 6; version += 1; guessAttempts = 2;
        return send(res, 200, {});
      }
      guessAttempts -= 1; version += 1;
      if (guessAttempts <= 0) {
        phaseIdx = 6; guessAttempts = 2;
        lastOutcome = { outcome: 'unclear', points_delta: 0, message: 'Too tricky — no points, no harm.' };
      }
      return send(res, 200, {});
    });
    return undefined;
  }
  if (method === 'GET' && url.startsWith('/api/leaderboard')) {
    // evolve the board so the TV's diff-driven beats fire in dev:
    // scores climb, ranks swap, and a new player eventually walks in
    boardPolls += 1;
    if (boardPolls % 3 === 0) {
      const row = board[1 + (boardPolls % (board.length - 1))];
      if (row) row.score += 30;
      board.sort((a, b) => b.score - a.score);
    }
    if (boardPolls === 8) board.push({ nickname: 'Kesar Comet', score: 205 });
    return send(res, 200, { entries: board.slice(0, 15) });
  }
  if (method === 'GET' && url.startsWith('/api/metrics')) {
    return send(res, 200, {
      ...metricsBase,
      validated_pairs: metricsBase.validated_pairs + boardPolls * 2,
      training_eligible_pairs: metricsBase.training_eligible_pairs + boardPolls,
    });
  }
  // playable sample clip for the guesser (2s two-tone WAV; browsers play by content-type)
  if (method === 'GET' && url.startsWith('/media/audio/')) {
    const rate = 16000, secs = 2, n = rate * secs;
    const buf = Buffer.alloc(44 + n * 2);
    buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
    buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
    buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28);
    buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36);
    buf.writeUInt32LE(n * 2, 40);
    for (let i = 0; i < n; i += 1) {
      const f = i < n / 2 ? 320 : 240;
      buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * f * i) / rate) * 9000), 44 + i * 2);
    }
    res.writeHead(200, { 'Content-Type': 'audio/wav', 'Access-Control-Allow-Origin': '*' });
    return res.end(buf);
  }

  // sample card art so the speaker/guesser screens have a real image to show
  if (method === 'GET' && url.startsWith('/media/decks/')) {
    res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Access-Control-Allow-Origin': '*' });
    return res.end(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">
      <rect width="400" height="400" fill="#e8d9c3"/>
      <ellipse cx="200" cy="330" rx="120" ry="18" fill="#c9b598"/>
      <path d="M140 150 q-40 60 -20 120 q15 45 80 45 q65 0 80 -45 q20 -60 -20 -120 q-15 -25 -60 -25 q-45 0 -60 25z" fill="#a0522d"/>
      <path d="M145 152 q-8 14 -12 30 q40 18 134 0 q-4 -16 -12 -30 q-25 -18 -55 -18 q-30 0 -55 18z" fill="#8b4022"/>
      <ellipse cx="200" cy="128" rx="52" ry="14" fill="#7a3418"/>
      <ellipse cx="200" cy="124" rx="44" ry="10" fill="#5c2812"/>
      <path d="M160 200 q-15 40 -5 75" stroke="#c97f4f" stroke-width="10" fill="none" stroke-linecap="round" opacity="0.5"/>
    </svg>`);
  }

  // debug helper: jump phases from the browser console / tests
  const jump = url.match(/^\/mock\/phase\/(\w+)$/);
  if (jump && CYCLE.includes(jump[1])) {
    phaseIdx = CYCLE.indexOf(jump[1]); version += 1;
    return send(res, 200, { ok: true, phase: jump[1] });
  }
  send(res, 404, { error: 'not found' });
}).listen(8787, () => console.log('mock server on :8787'));
