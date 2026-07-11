/**
 * Dependency-free validators for mock fixtures and TV helpers against the
 * frozen contracts in contracts/api_types.py.
 *
 * Used by scripts/contract-check.test.mjs (node --test). No game logic —
 * only shape and corpus-safety invariants the frontend must preserve.
 */

import { NO_LABEL_PHASES } from '../src/lib/constants.js';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PHASES = new Set([
  'onboarding', 'queued', 'speaking_view_image', 'speaking_confirm_label',
  'waiting_partner', 'guessing', 'round_result', 'session_done',
]);

/**
 * @param {unknown} value
 * @param {string} path
 * @returns {string[]} Human-readable validation errors (empty = ok).
 */
export function validatePlayerState(value, path = 'player') {
  const errors = [];
  if (!value || typeof value !== 'object') return [`${path} must be an object`];
  const p = /** @type {Record<string, unknown>} */ (value);
  if (typeof p.nickname !== 'string' || !p.nickname) errors.push(`${path}.nickname required`);
  if (typeof p.score !== 'number') errors.push(`${path}.score must be a number`);
  if (p.rank !== null && typeof p.rank !== 'number') errors.push(`${path}.rank must be number or null`);
  if (typeof p.rounds_played !== 'number') errors.push(`${path}.rounds_played must be a number`);
  if (typeof p.rounds_cap !== 'number') errors.push(`${path}.rounds_cap must be a number`);
  return errors;
}

/**
 * @param {unknown} value
 * @param {string} name - Fixture filename stem for error messages.
 * @returns {string[]}
 */
export function validateStateFixture(value, name) {
  const errors = [];
  if (!value || typeof value !== 'object') return [`${name}: not an object`];
  const s = /** @type {Record<string, unknown>} */ (value);

  if (typeof s.state_version !== 'number') errors.push(`${name}: state_version must be a number`);
  if (typeof s.phase !== 'string' || !PHASES.has(s.phase)) errors.push(`${name}: invalid phase`);
  errors.push(...validatePlayerState(s.player, `${name}.player`));

  if (s.pair !== null && s.pair !== undefined) {
    const pair = /** @type {Record<string, unknown>} */ (s.pair);
    if (typeof pair.partner_nickname !== 'string') errors.push(`${name}.pair.partner_nickname required`);
    if (typeof pair.common_lang !== 'string') errors.push(`${name}.pair.common_lang required`);
  }

  const phase = /** @type {string} */ (s.phase);
  const turn = s.turn;
  if (turn !== null && turn !== undefined) {
    const t = /** @type {Record<string, unknown>} */ (turn);
    if (Array.isArray(t.options)) {
      for (const [i, opt] of t.options.entries()) {
        const o = /** @type {Record<string, unknown>} */ (opt);
        if (typeof o.id !== 'string' || !UUID_RE.test(o.id)) {
          errors.push(`${name}.turn.options[${i}].id must be a UUID string`);
        }
        if (typeof o.text !== 'string' || !o.text) {
          errors.push(`${name}.turn.options[${i}].text required`);
        }
      }
    }
    if (NO_LABEL_PHASES.includes(phase) && t.label != null) {
      errors.push(`${name}: label must be null/absent during ${phase} (no-label-before-audio)`);
    }
    if (phase === 'speaking_confirm_label' && (!t.label || typeof /** @type {Record<string, unknown>} */ (t.label).text !== 'string')) {
      errors.push(`${name}: speaking_confirm_label requires turn.label.text`);
    }
    if (phase === 'speaking_view_image' && t.label != null) {
      errors.push(`${name}: speaking_view_image must not expose turn.label`);
    }
  }

  if (!Array.isArray(s.leaderboard_top)) errors.push(`${name}: leaderboard_top must be an array`);

  return errors;
}

/**
 * @param {unknown} value
 * @returns {string[]}
 */
export function validateLeaderboardResponse(value) {
  const errors = [];
  if (!value || typeof value !== 'object') return ['leaderboard: not an object'];
  const b = /** @type {Record<string, unknown>} */ (value);
  if ('top' in b) errors.push('leaderboard: legacy "top" field — use "entries"');
  if (!Array.isArray(b.entries)) errors.push('leaderboard.entries must be an array');
  else {
    for (const [i, row] of b.entries.entries()) {
      const r = /** @type {Record<string, unknown>} */ (row);
      if (typeof r.nickname !== 'string') errors.push(`leaderboard.entries[${i}].nickname required`);
      if (typeof r.score !== 'number') errors.push(`leaderboard.entries[${i}].score must be a number`);
    }
  }
  return errors;
}

/**
 * @param {unknown} value
 * @returns {string[]}
 */
export function validateMetricsResponse(value) {
  const errors = [];
  if (!value || typeof value !== 'object') return ['metrics: not an object'];
  const m = /** @type {Record<string, unknown>} */ (value);
  if ('clips' in m) errors.push('metrics: legacy "clips" field — use validated_pairs');
  if (typeof m.languages === 'number') errors.push('metrics.languages must be a string array, not a number');
  const requiredNumbers = ['validated_pairs', 'training_eligible_pairs', 'language_count'];
  for (const key of requiredNumbers) {
    if (typeof m[key] !== 'number') errors.push(`metrics.${key} must be a number`);
  }
  if (!Array.isArray(m.languages)) errors.push('metrics.languages must be an array');
  const optionalNumbers = [
    'cost_per_validated_sample_usd',
    'gauntlet_pass_rate',
    'deck_images_per_minute',
    'deck_cost_per_image_usd',
  ];
  for (const key of optionalNumbers) {
    if (key in m && m[key] !== null && typeof m[key] !== 'number') {
      errors.push(`metrics.${key} must be a number when present`);
    }
  }
  return errors;
}

/**
 * Assert onboarding fixture ships a non-null PlayerState (backend contract).
 *
 * @param {unknown} onboarding
 * @returns {string[]}
 */
export function validateOnboardingPlayer(onboarding) {
  if (!onboarding || typeof onboarding !== 'object') return ['onboarding: missing'];
  const s = /** @type {Record<string, unknown>} */ (onboarding);
  if (s.phase !== 'onboarding') return ['onboarding: wrong phase'];
  if (s.player == null) return ['onboarding.player must be non-null PlayerState'];
  return validatePlayerState(s.player, 'onboarding.player');
}
