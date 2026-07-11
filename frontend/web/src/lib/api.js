// Thin API layer. The server is the source of truth for ALL game logic;
// this file only moves bytes and holds the session token.
const TOKEN_KEY = 'ddf_session_token';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.json);
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    // Any 401 → back to join screen (contract §2.2)
    window.dispatchEvent(new Event('ddf:unauthorized'));
    throw new ApiError(401, 'unauthorized');
  }
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return res.json();
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const api = {
  join: (payload) => request('/api/join', { method: 'POST', json: payload }),
  state: () => request('/api/state'),
  pairRequest: (signal) => request('/api/pair/request', {
    method: 'POST',
    json: {},
    signal,
  }),
  uploadAudio: (blob, filename) => {
    const form = new FormData();
    form.append('file', blob, filename);
    return request('/api/turn/audio', { method: 'POST', body: form });
  },
  confirmLabel: () => request('/api/turn/confirm-label', { method: 'POST', json: {} }),
  guess: (optionId) => request('/api/turn/guess', { method: 'POST', json: { option_id: optionId } }),
};
