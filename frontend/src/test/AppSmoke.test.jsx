/**
 * Renders the real screens against a mocked backend.
 *
 * A passing `vite build` only proves the code compiles. This proves the pages
 * actually mount, fetch, and render their data without a runtime error.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppLayout } from '../layouts/AppLayout';
import { DashboardPage } from '../pages/DashboardPage';
import { UploadPage } from '../pages/UploadPage';
import { MeetingsPage } from '../pages/MeetingsPage';
import { MeetingDetailPage } from '../pages/MeetingDetailPage';
import { ToastProvider } from '../hooks/useToast';

const MEETING = {
  id: 'm-1',
  title: 'Mobile launch sync',
  original_filename: 'launch.mp4',
  media_kind: 'video',
  file_size_bytes: 12 * 1024 * 1024,
  duration_seconds: 615,
  status: 'COMPLETED',
  has_transcript: true,
  has_intelligence: true,
  error_code: null,
  error_message: null,
  created_at: '2026-05-01T10:00:00Z',
  segment_count: 2,
  transcript_word_count: 18,
  transcript_model: 'base',
};

const TRANSCRIPT = {
  meeting_id: 'm-1',
  text: 'Welcome everyone. Ravi will handle the API integration.',
  segments: [
    { segment_index: 0, start_time: 0, end_time: 8, text: 'Welcome everyone.', speaker: null },
    { segment_index: 1, start_time: 9, end_time: 22, text: 'Ravi will handle the API integration.', speaker: null },
  ],
  language: 'en',
  duration_seconds: 615,
  model: 'base',
  word_count: 18,
};

const INTELLIGENCE = {
  meeting_id: 'm-1',
  summary: 'The team discussed the mobile application launch.',
  key_points: [{ id: 'k1', text: 'Launch is on track' }],
  decisions: [{ id: 'd1', text: 'Continue with the planned launch date', context: null }],
  participants: [
    { id: 'p1', name: 'Ravi', normalized_name: 'ravi', role: null, is_unknown: false, mention_count: 2, aliases: [] },
  ],
  action_items: [
    {
      id: 'a1',
      task: 'Complete API integration',
      assigned_to: 'Ravi',
      deadline: 'Friday',
      priority: 'high',
      status: 'pending',
      context: null,
    },
  ],
  model: 'grok-4-fast',
  chunk_count: 1,
};

/** Routes every URL the screens call to a canned payload. */
function mockBackend() {
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

      if (path.includes('/api/health')) {
        return send({
          status: 'ok',
          version: '1.0.0',
          environment: 'development',
          ffmpeg_available: true,
          whisper_backend: 'faster-whisper',
          whisper_model: 'base',
          grok_configured: true,
          supabase_configured: true,
          warnings: [],
        });
      }
      if (path.includes('/api/config/formats')) {
        return send({
          audio: ['mp3', 'wav'],
          video: ['mp4', 'mov'],
          accept_attribute: '.mp3,.wav,.mp4,.mov',
          max_upload_size_mb: 200,
        });
      }
      if (path.includes('/api/meetings/dashboard')) {
        return send({
          total_meetings: 3,
          completed_meetings: 2,
          processing_meetings: 0,
          failed_meetings: 0,
          total_action_items: 4,
          open_action_items: 3,
          total_participants: 5,
          total_transcribed_minutes: 42.5,
          recent_meetings: [MEETING],
        });
      }
      if (path.includes('/transcript')) return send(TRANSCRIPT);
      if (path.includes('/intelligence')) return send({ meeting_id: 'm-1', intelligence: INTELLIGENCE });
      if (path.match(/\/api\/meetings\/m-1$/)) return send(MEETING);
      if (path.includes('/api/meetings')) return send({ meetings: [MEETING], total: 1 });
      return send(null);
    })
  );
}

function renderAt(path, element, routePath) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path={routePath} element={element} />
          </Route>
        </Routes>
      </ToastProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  mockBackend();
});

describe('application screens', () => {
  it('renders the dashboard with live statistics', async () => {
    renderAt('/', <DashboardPage />, '/');
    expect(await screen.findByText('Mobile launch sync')).toBeInTheDocument();
    expect(screen.getByText('Open action items')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('All services ready')).toBeInTheDocument());
  });

  it('renders the upload screen with the supported formats from the API', async () => {
    renderAt('/upload', <UploadPage />, '/upload');
    expect(await screen.findByText(/Drop a recording here/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/Supported: mp3, wav, mp4, mov/)).toBeInTheDocument()
    );
  });

  it('renders the meetings list', async () => {
    renderAt('/meetings', <MeetingsPage />, '/meetings');
    expect(await screen.findByText('Mobile launch sync')).toBeInTheDocument();
    expect(screen.getByText('1 meeting')).toBeInTheDocument();
  });

  it('renders a meeting with its transcript and intelligence', async () => {
    renderAt('/meetings/m-1', <MeetingDetailPage />, '/meetings/:meetingId');

    expect(await screen.findByRole('heading', { name: 'Mobile launch sync' })).toBeInTheDocument();
    expect(screen.getByText(/Welcome everyone\./)).toBeInTheDocument();

    // Switch to the intelligence tab and check the analysis rendered.
    fireEvent.click(screen.getByRole('tab', { name: 'Meeting intelligence' }));
    await waitFor(() =>
      expect(screen.getByText('The team discussed the mobile application launch.')).toBeInTheDocument()
    );
    expect(screen.getByText('Complete API integration')).toBeInTheDocument();
    expect(screen.getByText('Continue with the planned launch date')).toBeInTheDocument();
  });
});
