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

async function adminRequest(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const key = getAdminKey();
  if (key) headers['X-Deck-Admin-Key'] = key;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.json);
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    clearAdminKey();
    window.dispatchEvent(new Event('ddf:admin-unauthorized'));
    throw new AdminApiError(401, 'unauthorized');
  }
  if (res.status === 503) {
    throw new AdminApiError(503, 'admin_not_configured');
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

export const adminApi = {
  listDecks: () => adminRequest('/api/admin/decks'),
  getDeck: (deckId) => adminRequest(`/api/admin/decks/${deckId}`),
  generateDeck: (payload) => adminRequest('/api/admin/decks', {
    method: 'POST',
    json: payload,
  }),
  activateDeck: (deckId) => adminRequest(`/api/admin/decks/${deckId}/activate`, {
    method: 'POST',
    json: {},
  }),
  metrics: () => fetch('/api/metrics').then((res) => {
    if (!res.ok) throw new AdminApiError(res.status, `HTTP ${res.status}`);
    return res.json();
  }),
  funnel: () => adminRequest('/api/admin/pipeline/funnel'),
  apiCalls: ({ limit = 25, operation } = {}) => {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (operation) params.set('operation', operation);
    const q = params.toString();
    return adminRequest(`/api/admin/api-calls${q ? `?${q}` : ''}`);
  },
  worker: () => adminRequest('/api/admin/worker'),
  jobs: ({ limit = 20, status } = {}) => {
    const params = new URLSearchParams();
    if (limit) params.set('limit', String(limit));
    if (status) params.set('status', status);
    const q = params.toString();
    return adminRequest(`/api/admin/jobs${q ? `?${q}` : ''}`);
  },
};
