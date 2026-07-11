/**
 * Frontend contract tests — validates mock fixtures and TV metric helpers
 * against contracts/api_types.py without npm test dependencies.
 *
 * Run: npm run test:contract
 * Or:  node --test scripts/contract-check.test.mjs
 */

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ADMIN_TUNE_POLL_MS,
  ADMIN_TUNE_RECORDING_MAX_MS,
  ADMIN_TUNE_RECORDING_MIN_MS,
  PAIR_REQUEST_QUEUE_RETRY_MS,
  PAIR_REQUEST_RETRY_BASE_MS,
  PAIR_REQUEST_RETRY_JITTER_MS,
  PAIR_REQUEST_RETRY_MAX_MS,
} from '../src/lib/constants.js';
import { buildMetricsTickerLines } from '../src/lib/metricsTicker.js';
import {
  createPairRequestRetryLoop,
  isTransientPairRequestError,
  pairRequestRetryDelay,
} from '../src/lib/pairRequestRetry.js';
import {
  validateLeaderboardResponse,
  validateMetricsResponse,
  validateOnboardingPlayer,
  validateStateFixture,
} from './contract-schema.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const fixturesDir = join(root, 'mock', 'fixtures');

const STATE_FIXTURES = readdirSync(fixturesDir)
  .filter((f) => f.endsWith('.json') && !['leaderboard.json', 'metrics.json'].includes(f));

test('state fixtures match StateResponse contract', () => {
  for (const file of STATE_FIXTURES) {
    const data = JSON.parse(readFileSync(join(fixturesDir, file), 'utf8'));
    const errors = validateStateFixture(data, file.replace('.json', ''));
    assert.deepEqual(errors, [], errors.join('\n'));
  }
});

test('onboarding fixture has non-null PlayerState', () => {
  const onboarding = JSON.parse(readFileSync(join(fixturesDir, 'onboarding.json'), 'utf8'));
  const errors = validateOnboardingPlayer(onboarding);
  assert.deepEqual(errors, [], errors.join('\n'));
});

test('speaking_view_image never exposes label (corpus safety)', () => {
  const data = JSON.parse(readFileSync(join(fixturesDir, 'speaking_view_image.json'), 'utf8'));
  assert.equal(data.phase, 'speaking_view_image');
  assert.equal(data.turn?.label, null);
});

test('speaker confirm copy references target concept, not speech translation', () => {
  const source = readFileSync(join(root, 'src', 'screens', 'Speaker.jsx'), 'utf8');
  assert.match(source, /Your target concept was:/);
  assert.match(source, /Did your recording describe this\?/);
  assert.equal(source.includes('You said'), false);
  // Label remains confirm-phase only: SpeakerStage must not take a label prop.
  assert.match(source, /function SpeakerStage\(\{ rec, native \}\)/);
  assert.match(source, /function ConfirmPanel\(\{ label, clipUrl \}\)/);
});

test('guessing options use UUID string ids', () => {
  const data = JSON.parse(readFileSync(join(fixturesDir, 'guessing.json'), 'utf8'));
  for (const opt of data.turn.options) {
    assert.match(opt.id, /^[0-9a-f-]{36}$/i);
    assert.equal(typeof opt.id, 'string');
  }
});

test('leaderboard fixture uses entries not top', () => {
  const data = JSON.parse(readFileSync(join(fixturesDir, 'leaderboard.json'), 'utf8'));
  const errors = validateLeaderboardResponse(data);
  assert.deepEqual(errors, [], errors.join('\n'));
  assert.ok(data.entries.length > 0);
});

test('metrics fixture matches MetricsResponse', () => {
  const data = JSON.parse(readFileSync(join(fixturesDir, 'metrics.json'), 'utf8'));
  const errors = validateMetricsResponse(data);
  assert.deepEqual(errors, [], errors.join('\n'));
});

test('TV ticker builder consumes canonical metrics fields', () => {
  const metrics = JSON.parse(readFileSync(join(fixturesDir, 'metrics.json'), 'utf8'));
  const lines = buildMetricsTickerLines(metrics);
  assert.ok(lines.some((l) => l.includes('validated pairs')));
  assert.ok(lines.some((l) => l.includes('training-eligible')));
  assert.ok(!lines.some((l) => l.includes('clips')));
});

test('pair request retries only network and 5xx failures', () => {
  assert.equal(isTransientPairRequestError(new TypeError('network failed')), true);
  assert.equal(isTransientPairRequestError({ status: 500 }), true);
  assert.equal(isTransientPairRequestError({ status: 503 }), true);
  assert.equal(isTransientPairRequestError({ status: 401 }), false);
  assert.equal(isTransientPairRequestError({ status: 404 }), false);
});

test('pair request retry delay is jittered and bounded', () => {
  assert.equal(pairRequestRetryDelay(0, 0), PAIR_REQUEST_RETRY_BASE_MS);
  assert.equal(
    pairRequestRetryDelay(0, 0.5),
    PAIR_REQUEST_RETRY_BASE_MS + Math.floor(PAIR_REQUEST_RETRY_JITTER_MS * 0.5),
  );
  assert.equal(pairRequestRetryDelay(99, 1), PAIR_REQUEST_RETRY_MAX_MS);
});

test('queued matchmaking responses retry until the backend reports matched', async () => {
  const responses = [{ status: 'queued' }, { status: 'matched' }];
  const scheduled = [];
  let calls = 0;
  const loop = createPairRequestRetryLoop({
    request: async () => {
      const response = responses[calls];
      calls += 1;
      return response;
    },
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false };
      scheduled.push(timer);
      return timer;
    },
    cancelSchedule: (timer) => { timer.cancelled = true; },
    random: () => 0.5,
  });

  loop.start();
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(scheduled.length, 1);
  assert.equal(
    scheduled[0].delay,
    PAIR_REQUEST_QUEUE_RETRY_MS + Math.floor(PAIR_REQUEST_RETRY_JITTER_MS * 0.5),
  );

  scheduled[0].callback();
  await Promise.resolve();
  assert.equal(calls, 2);
  assert.equal(scheduled.length, 1);
  loop.stop();
});

test('pair request retry loop never overlaps requests', async () => {
  const pending = [];
  const scheduled = [];
  let calls = 0;
  let latestSignal = null;
  const loop = createPairRequestRetryLoop({
    request: (signal) => {
      calls += 1;
      latestSignal = signal;
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false };
      scheduled.push(timer);
      return timer;
    },
    cancelSchedule: (timer) => { timer.cancelled = true; },
    random: () => 0.5,
  });

  loop.start();
  loop.start();
  assert.equal(calls, 1);

  pending[0].reject({ status: 503 });
  await Promise.resolve();
  assert.equal(scheduled.length, 1);
  assert.equal(
    scheduled[0].delay,
    PAIR_REQUEST_RETRY_BASE_MS + Math.floor(PAIR_REQUEST_RETRY_JITTER_MS * 0.5),
  );

  scheduled[0].callback();
  await Promise.resolve();
  assert.equal(calls, 2);
  loop.stop();
  assert.equal(latestSignal.aborted, true);
});

test('pair request retry loop cancels timer and never retries 401', async () => {
  const transientTimers = [];
  const transientLoop = createPairRequestRetryLoop({
    request: async () => { throw { status: 500 }; },
    schedule: (callback, delay) => {
      const timer = { callback, delay, cancelled: false };
      transientTimers.push(timer);
      return timer;
    },
    cancelSchedule: (timer) => { timer.cancelled = true; },
    random: () => 0,
  });

  transientLoop.start();
  await Promise.resolve();
  assert.equal(transientTimers.length, 1);
  transientLoop.stop();
  assert.equal(transientTimers[0].cancelled, true);

  let unauthorizedTimerCount = 0;
  const unauthorizedLoop = createPairRequestRetryLoop({
    request: async () => { throw { status: 401 }; },
    schedule: () => {
      unauthorizedTimerCount += 1;
      return 1;
    },
  });
  unauthorizedLoop.start();
  await Promise.resolve();
  assert.equal(unauthorizedTimerCount, 0);
  unauthorizedLoop.stop();
});

test('admin prompt-to-deck presets cover 28 states and example themes', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'deckPresets.js'), 'utf8');
  assert.match(source, /export const INDIAN_STATES/);
  assert.match(source, /west-bengal/);
  assert.match(source, /andhra-pradesh/);
  assert.match(source, /EXAMPLE_PROMPTS/);
  assert.match(source, /Monsoon market/);
  assert.match(source, /Festival night/);
  assert.match(source, /Rural transport/);
  assert.match(source, /Coastal life/);
  assert.match(source, /Mountain village/);
  assert.match(source, /urban street/i);
});

test('admin API client exposes generateDeckFromPrompt', () => {
  const source = readFileSync(join(root, 'src', 'lib', 'adminApi.js'), 'utf8');
  assert.match(source, /generateDeckFromPrompt/);
  assert.match(source, /\/api\/admin\/decks\/from-prompt/);
});

test('admin Decks panel uses prompt form and centralized poll interval', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'AdminApp.jsx'), 'utf8');
  assert.match(source, /generateDeckFromPrompt/);
  assert.match(source, /ADMIN_DECK_POLL_MS/);
  assert.match(source, /Inventing concepts with Gemini/);
  assert.match(source, /Nano Banana 2 Lite/);
  assert.match(source, /admin-card-skeleton/);
  assert.match(source, /Estimated cost incurred/);
  assert.match(source, /admin-cost-spotlight/);
  assert.match(source, /admin-image-modal/);
  assert.match(source, /Open \$\{card\.label_en\} image/);
  assert.match(source, /No personal information is requested/);
  assert.match(source, /Gemma 4 training and hosting run locally/);
  assert.match(source, /Advanced · paste concepts JSON/);
});

test('tune contract declares safe overview, artifacts, jobs, and comparisons', () => {
  const source = readFileSync(
    join(root, '..', '..', 'contracts', 'api_types.py'),
    'utf8',
  );
  assert.match(source, /class AdminTuneOverview\(BaseModel\)/);
  assert.match(source, /class AdminTuneArtifactMetadata\(BaseModel\)/);
  assert.match(source, /class AdminTuneJobDetail\(AdminTuneJobSummary\)/);
  assert.match(source, /full_adapter_ready: bool/);
  assert.match(source, /readiness_reason: str \| None/);
  assert.match(source, /heldout_comparisons: list\[AdminTuneHeldoutComparison\]/);
  assert.match(source, /adapter_sha256:/);
  assert.match(source, /failure_reason:/);
});

test('tune route source matches frontend endpoint and multipart field names', () => {
  const source = readFileSync(
    join(root, '..', '..', 'app', 'api', 'admin_tune.py'),
    'utf8',
  );
  assert.match(source, /prefix="\/api\/admin\/tune"/);
  assert.match(source, /@router\.get\("\/overview"/);
  assert.match(source, /"\/jobs\/train-smoke"/);
  assert.match(source, /"\/jobs\/infer-live"/);
  assert.match(source, /audio: Annotated\[UploadFile, File/);
  assert.match(source, /native_language: Annotated\[str, Form/);
  assert.match(source, /"\/samples\/\{sample_id\}\/audio"/);
  assert.match(source, /"\/jobs\/\{job_id\}"/);
});

test('admin API exposes authenticated tune JSON, multipart, and Blob calls', () => {
  const source = readFileSync(join(root, 'src', 'lib', 'adminApi.js'), 'utf8');
  assert.match(source, /export function adminJson/);
  assert.match(source, /export function adminMultipart/);
  assert.match(source, /export function adminBlob/);
  assert.match(source, /X-Deck-Admin-Key/);
  assert.match(source, /\/api\/admin\/tune\/overview/);
  assert.match(source, /\/api\/admin\/tune\/jobs\/train-smoke/);
  assert.match(source, /\/api\/admin\/tune\/jobs\/infer-live/);
  assert.match(source, /\/api\/admin\/tune\/samples\/\$\{encodeURIComponent\(sampleId\)\}\/audio/);
  assert.match(source, /responseType: 'blob'/);
  assert.doesNotMatch(source, /[?&](?:key|admin_key)=/);
});

test('Tune panel separates proof from verified inference and cleans media', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'TunePanel.jsx'), 'utf8');
  assert.match(source, /ADMIN_TUNE_POLL_MS/);
  assert.match(source, /Live training proof — not used for quality/);
  assert.match(source, /Verified full adapter — used for inference/);
  assert.match(source, /Precomputed · approved held-out sample/);
  assert.match(source, /Live result · actual model output/);
  assert.match(source, /No ground truth is available for an unscripted live clip/);
  assert.match(source, /liveInput/);
  assert.match(source, /MediaRecorder/);
  assert.match(source, /new FormData\(\)/);
  assert.match(source, /form\.append\(\s*'audio'/);
  assert.match(source, /form\.append\('native_language'/);
  assert.match(source, /URL\.revokeObjectURL/);
  assert.match(source, /getTracks\(\)\.forEach\(\(track\) => track\.stop\(\)\)/);
  assert.match(source, /microphone audio is temporary/);
  assert.match(source, /never enters training data/);
  assert.doesNotMatch(source, /accuracy/i);
  assert.doesNotMatch(source, /unsloth\/gemma/i);
});

test('Tune panel obeys authoritative readiness and exact comparison matching', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'TunePanel.jsx'), 'utf8');
  assert.match(source, /const verifiedReady = overview\?\.full_adapter_ready === true/);
  assert.match(source, /overview\?\.readiness_reason/);
  assert.match(source, /Backend inference gate closed/);
  assert.match(source, /Published full-artifact metrics/);
  assert.match(source, /does not enable inference unless the backend explicitly publishes/);
  assert.match(source, /candidate\.sample_id === sample\.sample_id/);
  assert.match(source, /exactMatches\.length === 1/);
  assert.match(source, /disabled=\{!verifiedReady\}/);
  assert.match(source, /No approved qualitative comparison is published/);
  assert.doesNotMatch(source, /verifiedAdapter\.available && verifiedAdapter\.compatible/);
});

test('Tune job polling stops before another request after terminal status', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'TunePanel.jsx'), 'utf8');
  assert.match(
    source,
    /if \(TERMINAL_JOB_STATUSES\.has\(trackedStatus\)\) return undefined;\s+refreshJob\(trackedJobId\);/,
  );
  assert.match(source, /setInterval\(refreshOverview, ADMIN_TUNE_POLL_MS\)/);
});

test('Tune limits and polling are centralized and demo-bounded', () => {
  assert.equal(ADMIN_TUNE_POLL_MS, 2000);
  assert.equal(ADMIN_TUNE_RECORDING_MIN_MS, 1000);
  assert.equal(ADMIN_TUNE_RECORDING_MAX_MS, 8000);
});

test('AdminApp delegates only the Tune tab to the focused panel', () => {
  const source = readFileSync(join(root, 'src', 'admin', 'AdminApp.jsx'), 'utf8');
  assert.match(source, /import TunePanel from '.\/TunePanel\.jsx'/);
  assert.match(source, /return <TunePanel \/>/);
  assert.doesNotMatch(source, /Tune runbook \(terminal only\)/);
});
