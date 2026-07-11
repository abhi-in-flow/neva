/**
 * Admin API client for /admin surfaces.
 *
 * Attaches X-Deck-Admin-Key from sessionStorage. Never stores the key in the
 * URL. Clears the key on 401. Public metrics do not require the admin key.
 */

const ADMIN_KEY = 'ddf_deck_admin_key';

export function getAdminKey() {
  return sessionStorage.getItem(ADMIN_KEY) || '';
}

export function setAdminKey(value) {
  sessionStorage.setItem(ADMIN_KEY, value);
}

export function clearAdminKey() {
  sessionStorage.removeItem(ADMIN_KEY);
}

export class AdminApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Perform one authenticated admin request and normalize API failures.
 *
 * @param {string} path Relative same-origin API path.
 * @param {RequestInit & {json?: unknown, responseType?: 'json'|'blob'}} options Fetch options.
 * @returns {Promise<unknown>} Parsed JSON/text or a Blob, depending on responseType.
 */
async function adminRequest(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const key = getAdminKey();
  if (key) headers['X-Deck-Admin-Key'] = key;
  const responseType = options.responseType || 'json';
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.json);
  }
  const {
    json: _json,
    responseType: _responseType,
    ...fetchOptions
  } = options;
  const res = await fetch(path, { ...fetchOptions, headers });
  if (res.status === 401) {
    clearAdminKey();
    window.dispatchEvent(new Event('ddf:admin-unauthorized'));
    throw new AdminApiError(401, 'unauthorized');
  }
  if (res.status === 503) {
    const detail = await parseErrorDetail(res);
    throw new AdminApiError(503, 'admin_not_configured', detail);
  }
  if (responseType === 'blob' && res.ok) {
    return res.blob();
  }
  let body = null;
  const text = await res.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const detail = body && typeof body === 'object' ? body.detail : text;
    throw new AdminApiError(res.status, `HTTP ${res.status}`, detail);
  }
  return body;
}

/**
 * Parse an error response without assuming JSON.
 *
 * @param {Response} response Failed fetch response.
 * @returns {Promise<unknown>} Safe API detail or response text.
 */
async function parseErrorDetail(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === 'object' ? parsed.detail ?? parsed : parsed;
  } catch {
    return text;
  }
}

/**
 * Send or receive JSON through the authenticated admin boundary.
 *
 * @param {string} path Same-origin admin API path.
 * @param {RequestInit & {json?: unknown}} options Fetch options and optional JSON body.
 * @returns {Promise<unknown>} Parsed API response.
 */
export function adminJson(path, options = {}) {
  return adminRequest(path, { ...options, responseType: 'json' });
}

/**
 * Upload FormData without overriding the browser-generated multipart boundary.
 *
 * @param {string} path Same-origin admin API path.
 * @param {FormData} formData Multipart payload.
 * @param {RequestInit} options Additional fetch options.
 * @returns {Promise<unknown>} Parsed API response.
 */
export function adminMultipart(path, formData, options = {}) {
  return adminRequest(path, {
    ...options,
    method: options.method || 'POST',
    body: formData,
    responseType: 'json',
  });
}

/**
 * Fetch protected binary content with the admin key in a header.
 *
 * @param {string} path Same-origin admin API path.
 * @param {RequestInit} options Additional fetch options.
 * @returns {Promise<Blob>} Authenticated response body.
 */
export function adminBlob(path, options = {}) {
  return adminRequest(path, { ...options, responseType: 'blob' });
}

export const adminApi = {
  listDecks: () => adminJson('/api/admin/decks'),
  getDeck: (deckId) => adminJson(`/api/admin/decks/${deckId}`),
  generateDeck: (payload) => adminJson('/api/admin/decks', {
    method: 'POST',
    json: payload,
  }),
  generateDeckFromPrompt: (payload) => adminJson('/api/admin/decks/from-prompt', {
    method: 'POST',
    json: payload,
  }),
  activateDeck: (deckId) => adminJson(`/api/admin/decks/${deckId}/activate`, {
    method: 'POST',
    json: {},
  }),
  metrics: () => fetch('/api/metrics').then((res) => {
    if (!res.ok) throw new AdminApiError(res.status, `HTTP ${res.status}`);
    return res.json();
  }),
  funnel: () => adminJson('/api/admin/pipeline/funnel'),
  apiCalls: ({ limit = 25, operation } = {}) => {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (operation) params.set('operation', operation);
    const q = params.toString();
    return adminJson(`/api/admin/api-calls${q ? `?${q}` : ''}`);
  },
  worker: () => adminJson('/api/admin/worker'),
  jobs: ({ limit = 20, status } = {}) => {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (status) params.set('status', status);
    const q = params.toString();
    return adminJson(`/api/admin/jobs${q ? `?${q}` : ''}`);
  },
  tuneOverview: () => adminJson('/api/admin/tune/overview'),
  tuneJob: (jobId) => adminJson(
    `/api/admin/tune/jobs/${encodeURIComponent(jobId)}`,
  ),
  startTuneSmoke: () => adminJson('/api/admin/tune/jobs/train-smoke', {
    method: 'POST',
    json: {},
  }),
  inferTuneLive: (formData) => adminMultipart(
    '/api/admin/tune/jobs/infer-live',
    formData,
  ),
  tuneSampleAudio: (sampleId) => adminBlob(
    `/api/admin/tune/samples/${encodeURIComponent(sampleId)}/audio`,
  ),
};
