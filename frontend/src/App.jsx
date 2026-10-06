import { Route, Routes } from 'react-router-dom';
import { AppLayout } from './layouts/AppLayout';
import { DashboardPage } from './pages/DashboardPage';
import { UploadPage } from './pages/UploadPage';
import { MeetingsPage } from './pages/MeetingsPage';
import { MeetingDetailPage } from './pages/MeetingDetailPage';
import { AskPage } from './pages/AskPage';
import { NotFoundPage } from './pages/NotFoundPage';

import { IntegrationsPage } from './pages/IntegrationsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="upload" element={<UploadPage />} />
        <Route path="meetings" element={<MeetingsPage />} />
        <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
