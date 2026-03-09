import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/AppLayout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ProjectsPage } from './pages/ProjectsPage';
import { ProjectDetailPage } from './pages/ProjectDetailPage';
import { SessionsPage } from './pages/SessionsPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { BomPage } from './pages/BomPage';
import { TwinViewerPage } from './pages/TwinViewerPage';
import { DesignAssistantPage } from './pages/DesignAssistantPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<Navigate to="/projects" />} />
              <Route path="projects" element={<ProjectsPage />} />
              <Route path="projects/:id" element={<ProjectDetailPage />} />
              <Route path="sessions" element={<SessionsPage />} />
              <Route path="sessions/:id" element={<SessionDetailPage />} />
              <Route path="approvals" element={<ApprovalsPage />} />
              <Route path="bom" element={<BomPage />} />
              <Route path="twin" element={<TwinViewerPage />} />
              <Route path="assistant" element={<DesignAssistantPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
