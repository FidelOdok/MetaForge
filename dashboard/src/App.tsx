import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/AppLayout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/Toast';
import { ProjectsPage } from './pages/ProjectsPage';
import { ProjectDetailPage } from './pages/ProjectDetailPage';
import { SessionsPage } from './pages/SessionsPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { BomPage } from './pages/BomPage';
import { TwinViewerPage } from './pages/TwinViewerPage';
import { FilesPage } from './pages/FilesPage';
import { DesignAssistantPage } from './pages/DesignAssistantPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/projects" />} />
            <Route
              path="projects"
              element={
                <ErrorBoundary>
                  <ProjectsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="projects/:id"
              element={
                <ErrorBoundary>
                  <ProjectDetailPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="sessions"
              element={
                <ErrorBoundary>
                  <SessionsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="sessions/:id"
              element={
                <ErrorBoundary>
                  <SessionDetailPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="approvals"
              element={
                <ErrorBoundary>
                  <ApprovalsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="bom"
              element={
                <ErrorBoundary>
                  <BomPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="twin"
              element={
                <ErrorBoundary>
                  <TwinViewerPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="files"
              element={
                <ErrorBoundary>
                  <FilesPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="assistant"
              element={
                <ErrorBoundary>
                  <DesignAssistantPage />
                </ErrorBoundary>
              }
            />
          </Route>
        </Routes>
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
