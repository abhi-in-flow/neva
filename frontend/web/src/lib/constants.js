/**
 * Centralized frontend configuration for polling, venue TV, and contract checks.
 *
 * Game rules and phase transitions remain server-owned; these values only tune
 * client timing and presentation. Model names and scoring thresholds live in
 * the backend — never duplicate them here.
 */

/** Player app: GET /api/state interval in milliseconds (contract §2.1). */
export const STATE_POLL_MS = 2000;

/** Consecutive poll failures before showing the reconnecting pill. */
export const RECONNECT_FAIL_THRESHOLD = 3;

/** Initial delay before retrying a transient matchmaking transport failure. */
export const PAIR_REQUEST_RETRY_BASE_MS = 1500;

/** Maximum total delay between transient matchmaking transport retries. */
export const PAIR_REQUEST_RETRY_MAX_MS = 8000;

/** Delay between successful requests that remain queued. */
export const PAIR_REQUEST_QUEUE_RETRY_MS = 2000;

/** Random spread added to matchmaking retries to avoid synchronized traffic. */
export const PAIR_REQUEST_RETRY_JITTER_MS = 500;

/** Venue TV: GET /api/leaderboard interval in milliseconds. */
export const TV_LEADERBOARD_POLL_MS = 3000;

/** Venue TV: GET /api/metrics interval in milliseconds. */
export const TV_METRICS_POLL_MS = 10000;

/** Venue TV: duration each diff-driven beat banner stays visible. */
export const TV_BEAT_MS = 2000;

/** Venue TV: rows rendered on the podium + list (top 3 + remainder). */
export const TV_SHOW_ROWS = 8;

/** Venue TV: rotate ticker copy every N milliseconds. */
export const TV_TICKER_ROTATE_MS = 5000;

/** Admin Decks: poll generating deck detail for progressive card reveal. */
export const ADMIN_DECK_POLL_MS = 1500;

/** Canonical phase names mirrored from contracts/api_types.py Phase enum. */
export const PHASES = Object.freeze({
  ONBOARDING: 'onboarding',
  QUEUED: 'queued',
  SPEAKING_VIEW_IMAGE: 'speaking_view_image',
  SPEAKING_CONFIRM_LABEL: 'speaking_confirm_label',
  WAITING_PARTNER: 'waiting_partner',
  GUESSING: 'guessing',
  ROUND_RESULT: 'round_result',
  SESSION_DONE: 'session_done',
});

/** Phases where the speaker must never receive label text in the DOM. */
export const NO_LABEL_PHASES = Object.freeze([
  PHASES.ONBOARDING,
  PHASES.QUEUED,
  PHASES.SPEAKING_VIEW_IMAGE,
  PHASES.WAITING_PARTNER,
  PHASES.GUESSING,
  PHASES.ROUND_RESULT,
  PHASES.SESSION_DONE,
]);
