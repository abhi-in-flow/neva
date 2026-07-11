/**
 * Retry orchestration for the onboarding matchmaking request.
 *
 * The backend owns matchmaking and exposes an idempotent pair-request action.
 * This module only provides transport resilience: one request at a time,
 * bounded exponential delays with jitter for network/5xx failures, and prompt
 * cancellation when the caller leaves onboarding. Timer and randomness
 * dependencies are injectable so the behavior is testable without React.
 */

import {
  PAIR_REQUEST_QUEUE_RETRY_MS,
  PAIR_REQUEST_RETRY_BASE_MS,
  PAIR_REQUEST_RETRY_JITTER_MS,
  PAIR_REQUEST_RETRY_MAX_MS,
} from './constants.js';

/**
 * Return whether a failed pair request is safe to retry.
 *
 * @param {unknown} error - Error thrown by the API transport.
 * @returns {boolean} True only for network errors without a status or 5xx.
 */
export function isTransientPairRequestError(error) {
  const status = error && typeof error === 'object' ? error.status : undefined;
  return status == null || (typeof status === 'number' && status >= 500);
}

/**
 * Calculate a bounded exponential retry delay with deterministic-injectable
 * jitter.
 *
 * @param {number} failureIndex - Zero-based transient failure count.
 * @param {number} randomValue - Number normally supplied by Math.random().
 * @returns {number} Delay in milliseconds, never above the configured maximum.
 */
export function pairRequestRetryDelay(failureIndex, randomValue = Math.random()) {
  const safeFailureIndex = Math.max(0, Math.min(Math.floor(failureIndex), 16));
  const safeRandom = Math.max(0, Math.min(randomValue, 1));
  const exponentialDelay = Math.min(
    PAIR_REQUEST_RETRY_BASE_MS * (2 ** safeFailureIndex),
    PAIR_REQUEST_RETRY_MAX_MS,
  );
  const jitter = Math.floor(safeRandom * PAIR_REQUEST_RETRY_JITTER_MS);
  return Math.min(exponentialDelay + jitter, PAIR_REQUEST_RETRY_MAX_MS);
}

/**
 * Create a cancellable retry loop for POST /api/pair/request.
 *
 * @param {object} dependencies - Injected transport and scheduling functions.
 * @param {(signal: AbortSignal) => Promise<unknown>} dependencies.request
 *   Executes one idempotent pair request.
 * @param {(callback: () => void, delay: number) => unknown} [dependencies.schedule]
 *   Schedules a retry and returns a timer handle.
 * @param {(handle: unknown) => void} [dependencies.cancelSchedule]
 *   Cancels a previously scheduled retry.
 * @param {() => number} [dependencies.random]
 *   Supplies jitter; defaults to Math.random.
 * @returns {{start: () => void, stop: () => void}} Retry-loop controls.
 */
export function createPairRequestRetryLoop({
  request,
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancelSchedule = (handle) => clearTimeout(handle),
  random = Math.random,
}) {
  let running = false;
  let inFlight = false;
  let retryTimer = null;
  let failureIndex = 0;
  let abortController = null;

  /**
   * Schedule one non-overlapping follow-up request.
   *
   * @param {number} delay - Delay before the next attempt in milliseconds.
   * @returns {void}
   */
  function scheduleNext(delay) {
    if (!running || retryTimer !== null) return;
    retryTimer = schedule(() => {
      retryTimer = null;
      void attempt();
    }, delay);
  }

  /**
   * Execute one request and continue while the backend reports queued.
   *
   * @returns {Promise<void>} Resolves after this request attempt settles.
   */
  async function attempt() {
    if (!running || inFlight) return;

    inFlight = true;
    const controller = new AbortController();
    abortController = controller;

    try {
      const result = await request(controller.signal);
      failureIndex = 0;
      if (result?.status === 'queued') {
        const jitter = Math.floor(
          Math.max(0, Math.min(random(), 1)) * PAIR_REQUEST_RETRY_JITTER_MS,
        );
        scheduleNext(PAIR_REQUEST_QUEUE_RETRY_MS + jitter);
      } else {
        running = false;
      }
    } catch (error) {
      if (!running || error?.name === 'AbortError') return;
      if (!isTransientPairRequestError(error)) {
        running = false;
        return;
      }

      const delay = pairRequestRetryDelay(failureIndex, random());
      failureIndex += 1;
      scheduleNext(delay);
    } finally {
      inFlight = false;
      if (abortController === controller) abortController = null;
    }
  }

  /**
   * Start the retry loop once; repeated starts cannot overlap requests.
   *
   * @returns {void}
   */
  function start() {
    if (running) return;
    running = true;
    void attempt();
  }

  /**
   * Stop retries immediately, clear timers, and abort an active fetch.
   *
   * @returns {void}
   */
  function stop() {
    running = false;
    if (retryTimer !== null) {
      cancelSchedule(retryTimer);
      retryTimer = null;
    }
    abortController?.abort();
    abortController = null;
  }

  return { start, stop };
}
