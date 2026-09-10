import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, request } from '../services/api/client';

function mockResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('API client', () => {
  it('unwraps the data envelope on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => mockResponse({ success: true, data: { id: 'm-1' }, message: 'ok' }))
    );
    await expect(request('/api/meetings/m-1')).resolves.toEqual({ id: 'm-1' });
  });

  it('throws an ApiError carrying the backend error code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        mockResponse(
          { success: false, error: { code: 'UNSUPPORTED_FILE_TYPE', message: 'nope' } },
          { ok: false, status: 415 }
        )
      )
    );

    await expect(request('/api/meetings/upload', { method: 'POST' })).rejects.toMatchObject({
      code: 'UNSUPPORTED_FILE_TYPE',
      status: 415,
    });
  });

  it('turns error codes into messages a person can act on', () => {
    const error = new ApiError('DATABASE_NOT_CONFIGURED', 'raw server text');
    expect(error.displayMessage).toContain('Supabase is not configured');
  });

  it('reports a network failure rather than hanging', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      })
    );
    await expect(request('/api/health')).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
  });

  it('sends JSON bodies with the right content type', async () => {
    const fetchMock = vi.fn(async () => mockResponse({ success: true, data: null }));
    vi.stubGlobal('fetch', fetchMock);

    await request('/api/meetings/m-1/analyze', { method: 'POST', body: { force: true } });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(init.body).toBe('{"force":true}');
  });
});
