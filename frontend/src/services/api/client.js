/**
 * Central HTTP client. Every request in the app goes through here, so timeouts,
 * the response envelope and error translation are handled in exactly one place.
 *
 * The backend always answers with:
 *   { success: true,  data: {...}, message: "..." }
 *   { success: false, error: { code, message, details } }
 *
 * `request()` unwraps `data` on success and throws an ApiError on failure, so
 * calling code never has to check `success` by hand.
 */

const BASE_URL = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '');
const TIMEOUT_MS = Number(import.meta.env?.VITE_API_TIMEOUT_MS) || 900000;

/** Error codes turned into sentences a person can act on. */
const FRIENDLY_MESSAGES = {
  UNSUPPORTED_FILE_TYPE: 'That file type is not supported. Upload an audio or video recording.',
  FILE_TOO_LARGE: 'That file is larger than the upload limit.',
  EMPTY_FILE: 'That file is empty.',
  CORRUPTED_MEDIA: 'That file could not be read as audio or video.',
  FFMPEG_NOT_AVAILABLE: 'FFmpeg is not installed on the server, so recordings cannot be processed.',
  EMPTY_TRANSCRIPT: 'No speech was detected in this recording.',
  TRANSCRIPT_NOT_FOUND: 'This meeting has not been transcribed yet.',
  INTELLIGENCE_NOT_FOUND: 'This meeting has not been analysed yet.',
  MEETING_NOT_FOUND: 'That meeting no longer exists.',
  MEDIA_UNAVAILABLE: 'The recording is no longer on the server. Upload it again to transcribe it.',
  LLM_NOT_CONFIGURED: 'The Grok API key is not set on the server, so analysis is unavailable.',
  LLM_RATE_LIMITED: 'Grok is rate limiting requests. Wait a moment and try again.',
  LLM_INVALID_RESPONSE: 'The AI returned an unusable response. Try running the analysis again.',
  DATABASE_NOT_CONFIGURED: 'Supabase is not configured on the server, so nothing can be saved.',
  DATABASE_TABLE_MISSING: 'The database tables are missing. Run backend/database/schema.sql in Supabase.',
  DATABASE_ERROR: 'The database could not be reached.',
  NETWORK_ERROR: 'The backend could not be reached. Check that it is running.',
  TIMEOUT: 'The request took too long and was cancelled.',
};

export class ApiError extends Error {
  constructor(code, message, { status = 0, details = null } = {}) {
    super(message || FRIENDLY_MESSAGES[code] || 'Something went wrong.');
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }

  /** Message to show a user, preferring our friendly copy over raw server text. */
  get displayMessage() {
    return FRIENDLY_MESSAGES[this.code] || this.message;
  }
}

export function buildUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${BASE_URL}${suffix}`;
}

async function parseBody(response) {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function request(path, options = {}) {
  const { method = 'GET', body, signal, timeoutMs = TIMEOUT_MS, headers = {} } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort('timeout'), timeoutMs);
  if (signal) signal.addEventListener('abort', () => controller.abort(), { once: true });

  const init = { method, signal: controller.signal, headers: { ...headers } };

  if (body instanceof FormData) {
    init.body = body; // let the browser set the multipart boundary
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(buildUrl(path), init);
  } catch (error) {
    clearTimeout(timer);
    if (controller.signal.aborted) {
      throw new ApiError('TIMEOUT', FRIENDLY_MESSAGES.TIMEOUT);
    }
    throw new ApiError('NETWORK_ERROR', FRIENDLY_MESSAGES.NETWORK_ERROR, { details: String(error) });
  }
  clearTimeout(timer);

  const payload = await parseBody(response);

  if (!response.ok) {
    const error = payload?.error || {};
    throw new ApiError(error.code || `HTTP_${response.status}`, error.message, {
      status: response.status,
      details: error.details || null,
    });
  }

  if (payload && payload.success === false) {
    const error = payload.error || {};
    throw new ApiError(error.code || 'UNKNOWN', error.message, { details: error.details });
  }

  return payload ? payload.data : null;
}

export const get = (path, options) => request(path, { ...options, method: 'GET' });
export const post = (path, body, options) => request(path, { ...options, method: 'POST', body });
export const patch = (path, body, options) => request(path, { ...options, method: 'PATCH', body });
export const del = (path, options) => request(path, { ...options, method: 'DELETE' });

/**
 * Upload with real progress. `fetch` cannot report upload progress, so this one
 * call uses XMLHttpRequest - the progress bar shows actual bytes sent, never a
 * fake animation.
 */
export function uploadWithProgress(path, formData, { onProgress, timeoutMs = TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', buildUrl(path));
    xhr.timeout = timeoutMs;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }

      if (xhr.status >= 200 && xhr.status < 300 && payload?.success !== false) {
        resolve(payload ? payload.data : null);
        return;
      }
      const error = payload?.error || {};
      reject(
        new ApiError(error.code || `HTTP_${xhr.status}`, error.message, {
          status: xhr.status,
          details: error.details || null,
        })
      );
    };

    xhr.onerror = () => reject(new ApiError('NETWORK_ERROR', FRIENDLY_MESSAGES.NETWORK_ERROR));
    xhr.ontimeout = () => reject(new ApiError('TIMEOUT', FRIENDLY_MESSAGES.TIMEOUT));
    xhr.send(formData);
  });
}

/** Download a file the browser should save, preserving the server filename. */
export async function downloadFile(path, fallbackName) {
  const response = await fetch(buildUrl(path));
  if (!response.ok) {
    const payload = await parseBody(response);
    const error = payload?.error || {};
    throw new ApiError(error.code || `HTTP_${response.status}`, error.message, {
      status: response.status,
    });
  }

  const disposition = response.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match ? match[1] : fallbackName;

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}
