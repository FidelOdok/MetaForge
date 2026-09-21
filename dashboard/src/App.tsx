import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/AppLayout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/Toast';
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then(module => ({ default: module.ProjectsPage })));
const ProjectDetailPage = lazy(() => import('./pages/ProjectDetailPage').then(module => ({ default: module.ProjectDetailPage })));
const SessionsPage = lazy(() => import('./pages/SessionsPage').then(module => ({ default: module.SessionsPage })));
const SessionDetailPage = lazy(() => import('./pages/SessionDetailPage').then(module => ({ default: module.SessionDetailPage })));
const RunsPage = lazy(() => import('./pages/RunsPage').then(module => ({ default: module.RunsPage })));
const RunDetailPage = lazy(() => import('./pages/RunDetailPage').then(module => ({ default: module.RunDetailPage })));
const ApprovalsPage = lazy(() => import('./pages/ApprovalsPage').then(module => ({ default: module.ApprovalsPage })));
const BomPage = lazy(() => import('./pages/BomPage').then(module => ({ default: module.BomPage })));
const TwinViewerPage = lazy(() => import('./pages/TwinViewerPage').then(module => ({ default: module.TwinViewerPage })));
const FilesPage = lazy(() => import('./pages/FilesPage').then(module => ({ default: module.FilesPage })));
const KnowledgePage = lazy(() => import('./pages/KnowledgePage').then(module => ({ default: module.KnowledgePage })));
const SourceDetailPage = lazy(() => import('./pages/SourceDetailPage').then(module => ({ default: module.SourceDetailPage })));
const CompliancePage = lazy(() => import('./pages/CompliancePage').then(module => ({ default: module.CompliancePage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(module => ({ default: module.SettingsPage })));

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<div className="workspace-empty" role="status">Loading workspace…</div>}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/projects" replace />} />
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
              path="runs"
              element={
                <ErrorBoundary>
                  <RunsPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="runs/:id"
              element={
                <ErrorBoundary>
                  <RunDetailPage />
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
              path="knowledge"
              element={
                <ErrorBoundary>
                  <KnowledgePage />
                </ErrorBoundary>
              }
            />
            <Route
              path="knowledge/sources/:id"
              element={
                <ErrorBoundary>
                  <SourceDetailPage />
                </ErrorBoundary>
              }
            />
            <Route
              path="compliance"
              element={
                <ErrorBoundary>
                  <CompliancePage />
                </ErrorBoundary>
              }
            />
            <Route
              path="settings"
              element={
                <ErrorBoundary>
                  <SettingsPage />
                </ErrorBoundary>
              }
            />
          </Route>
          <Route path="*" element={<div className="workspace-empty"><h1>Page not found</h1><a className="text-action" href="/projects">Return to projects</a></div>} />
        </Routes>
        </Suspense>
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
