/* =============================================================================
   core/api.js — fetch wrapper for non-htmx calls
   -----------------------------------------------------------------------------
   CSRF parity with the V1 middleware (website/__init__.py validate_csrf_token):
   the token comes from the session via {{ csrf_token() }} and is accepted as
   the X-CSRFToken header (or a csrf_token form/JSON field). base.html renders
   it once into <meta name="csrf-token">; this module reads it lazily.

   Errors: throws ApiError { status, message, detail } where message is the
   server's 'error' / 'message' / 'detail' field when present, so pages can
   toast it directly.
   ========================================================================== */
let cachedToken = null;

export function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) {
    cachedToken = meta.content;
    return meta.content;
  }
  return cachedToken || '';
}

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function pickError(payload) {
  if (payload && typeof payload === 'object') {
    for (const key of ['error', 'message', 'detail', 'msg']) {
      const value = payload[key];
      if (typeof value === 'string' && value) return value;
    }
  }
  return null;
}

async function request(method, url, options = {}) {
  const { params, json, form, headers } = options;
  const target = params && Object.keys(params).length
    ? url + '?' + new URLSearchParams(params).toString()
    : url;

  const requestHeaders = Object.assign({
    'X-CSRFToken': getCsrfToken(),
    'X-Requested-With': 'XMLHttpRequest'
  }, headers);
  let body;
  if (json !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = form; /* FormData — the browser sets the multipart boundary */
  }

  const response = await fetch(target, {
    method,
    headers: requestHeaders,
    body,
    credentials: 'same-origin',
  });

  if (response.status === 204) return null;

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new ApiError(
      response.status,
      pickError(payload) || response.statusText,
      payload
    );
  }
  return payload;
}

export const api = {
  get: (url, params) => request('GET', url, { params }),
  post: (url, json, options) => request('POST', url, Object.assign({ json }, options)),
  patch: (url, json, options) => request('PATCH', url, Object.assign({ json }, options)),
  put: (url, json, options) => request('PUT', url, Object.assign({ json }, options)),
  remove: (url, options) => request('DELETE', url, options),
  del: (url, options) => request('DELETE', url, options),
  delete: (url, options) => request('DELETE', url, options),
  submit: (url, form, options) => request('POST', url, Object.assign({ form }, options)),
};

export default api;
