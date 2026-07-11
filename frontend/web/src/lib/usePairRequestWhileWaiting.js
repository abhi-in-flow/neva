/**
 * Keep matchmaking active while the backend reports onboarding or queued.
 *
 * The endpoint is idempotent and backend-owned. Repeating it closes the
 * concurrent-enqueue race where two compatible players can both observe each
 * other's locked queue rows and initially remain queued. One cancellable loop
 * runs per waiting episode and stops as soon as polling reports a turn phase.
 */

import { useEffect } from 'react';
import { api } from './api.js';
import { PHASES } from './constants.js';
import { createPairRequestRetryLoop } from './pairRequestRetry.js';

const MATCHMAKING_PHASES = new Set([PHASES.ONBOARDING, PHASES.QUEUED]);

/**
 * Request matchmaking until the player leaves a waiting phase.
 *
 * @param {string|null|undefined} phase - Current StateResponse.phase value.
 * @returns {void}
 */
export function usePairRequestWhileWaiting(phase) {
  useEffect(() => {
    if (!MATCHMAKING_PHASES.has(phase)) return undefined;

    const retryLoop = createPairRequestRetryLoop({
      request: (signal) => api.pairRequest(signal),
    });
    retryLoop.start();

    return retryLoop.stop;
  }, [phase]);
}
