const TOKEN_KEY = 'citalens-session';
const BASE = '/api/v1';

export const session = {
  get() {
    try { return JSON.parse(sessionStorage.getItem(TOKEN_KEY)); } catch { return null; }
  },
  set(value) { sessionStorage.setItem(TOKEN_KEY, JSON.stringify(value)); },
  clear() { sessionStorage.removeItem(TOKEN_KEY); },
};

export async function api(path, { body, ...options } = {}) {
  const token = session.get()?.access_token;
  const headers = new Headers(options.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (body !== undefined && !(body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${BASE}${path}`, {
    ...options, headers,
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail;
    try { detail = await response.json(); } catch { detail = {}; }
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('citalens:expired'));
    const data = detail.error || detail.detail || detail;
    const message = typeof data === 'string' ? data : data.message;
    const error = new Error(message || (response.status === 413 ? 'O PDF ultrapassa o tamanho permitido.' : `Não foi possível concluir a solicitação (${response.status}).`));
    error.correlationId = data.correlation_id || detail.correlation_id;
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  if (options.blob) return response.blob();
  return response.json();
}

export function query(values = {}) {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== '' && value !== null && value !== undefined) parameters.set(key, value);
  return parameters.size ? `?${parameters}` : '';
}

export async function download(path, filename) {
  const blob = await api(path, { blob: true });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function safeUrl(value) {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}
