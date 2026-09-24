/**
 * Milestone 3 screen, rendered against a mocked backend.
 *
 * Proves the page mounts, calls the right endpoints, and renders answers,
 * sources and search results - the things a passing `vite build` cannot tell
 * you. No real backend, no API keys.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AskPage } from '../pages/AskPage';
import { ToastProvider } from '../hooks/useToast';

const READY_STATUS = {
  embeddings_configured: true,
  vector_store_configured: true,
  ready: true,
  embedding_provider: 'fastembed',
  embedding_model: 'BAAI/bge-small-en-v1.5',
  embedding_dimensions: 384,
  index_name: 'meeting-knowledge',
  namespace: 'meetings',
  tracks_index_status: true,
  counts: { INDEXED: 3 },
};

const ANSWER = {
  question: 'What deadline was decided for the mobile application?',
  answer: 'The team decided to release the mobile application by Friday.',
  answer_found: true,
  confidence: 'high',
  sources: [
    {
      meeting_id: 'm-1',
      meeting_title: 'Mobile Application Planning',
      meeting_date: '2026-09-15T10:00:00Z',
      source_type: 'decision',
      excerpt: 'Decision made in the meeting: ship the app before the end of the week.',
      score: 0.93,
    },
  ],
  provider: 'groq',
  model: 'openai/gpt-oss-20b',
  searched_meetings: 1,
  took_ms: 820,
};

const SEARCH = {
  query: 'Which meeting discussed the database migration?',
  results: [
    {
      meeting_id: 'm-2',
      meeting_title: 'Platform Infrastructure Sync',
      meeting_date: '2026-09-10T09:00:00Z',
      status: 'COMPLETED',
      score: 0.91,
      matched_source_types: ['decision', 'transcript'],
      excerpt: 'The team agreed the Postgres schema move has to finish first.',
      matches: [],
    },
  ],
  total_matches: 2,
  took_ms: 640,
};

function mockBackend({ status = READY_STATUS } = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      const path = String(url);
      const send = (data) => ({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ success: true, data, message: 'ok' }),
      });

      if (path.includes('/api/knowledge/status')) return send(status);
      if (path.includes('/api/knowledge/index')) {
        return send({ processed: 3, indexed: 3, skipped_unchanged: 0, failed: 0, meetings: [] });
      }
      if (path.includes('/api/meetings/ask')) return send(ANSWER);
      if (path.includes('/api/meetings/search')) return send(SEARCH);
      return send({});
    })
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <AskPage />
      </ToastProvider>
    </MemoryRouter>
  );
}

describe('ask & search page', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows setup instructions when search is not configured', async () => {
    mockBackend({
      status: { ...READY_STATUS, ready: false, vector_store_configured: false, counts: {} },
    });
    renderPage();

    expect(await screen.findByText(/not configured yet/i)).toBeInTheDocument();
    expect(screen.getByText('PINECONE_API_KEY')).toBeInTheDocument();
  });

  it('answers a question and shows the source meeting', async () => {
    mockBackend();
    renderPage();

    const input = await screen.findByPlaceholderText(/what deadline was decided/i);
    fireEvent.change(input, {
      target: { value: 'What deadline was decided for the mobile application?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^ask$/i }));

    expect(await screen.findByText(/release the mobile application by Friday/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('Mobile Application Planning')).toBeInTheDocument();
    });
    expect(screen.getByText(/high confidence/i)).toBeInTheDocument();

    const asked = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(asked.some((url) => url.includes('/api/meetings/ask'))).toBe(true);
  });

  it('searches meetings and lists the matches', async () => {
    mockBackend();
    renderPage();

    fireEvent.click(await screen.findByRole('tab', { name: /search meetings/i }));

    const input = await screen.findByPlaceholderText(/which meeting discussed/i);
    fireEvent.change(input, { target: { value: 'database migration' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));

    expect(await screen.findByText('Platform Infrastructure Sync')).toBeInTheDocument();
    expect(screen.getByText(/Postgres schema move/i)).toBeInTheDocument();
    expect(screen.getByText(/91% match/i)).toBeInTheDocument();
  });

  it('offers indexing when nothing has been indexed yet', async () => {
    mockBackend({ status: { ...READY_STATUS, counts: {} } });
    renderPage();

    expect(await screen.findByText(/no meetings indexed yet/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /index my meetings/i }));

    await waitFor(() => {
      const called = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
      expect(called.some((url) => url.includes('/api/knowledge/index'))).toBe(true);
    });
  });

  it('does not search or ask anything on page load', async () => {
    mockBackend();
    renderPage();

    await screen.findByRole('tab', { name: /ask a question/i });
    const called = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(called.some((url) => url.includes('/api/meetings/ask'))).toBe(false);
    expect(called.some((url) => url.includes('/api/meetings/search'))).toBe(false);
  });
});
