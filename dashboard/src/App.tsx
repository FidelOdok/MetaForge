import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthGate } from './auth/AuthGate';
import { AuthProvider } from './auth/AuthProvider';
import { OnboardingGate } from './onboarding/OnboardingGate';
import { AppLayout } from './components/layout/AppLayout';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toaster } from './components/ui/Toast';

/** Code-split a page module by its named export. */
function page<K extends string>(
  load: () => Promise<Record<K, ComponentType>>,
  name: K,
): LazyExoticComponent<ComponentType> {
  return lazy(() => load().then((m) => ({ default: m[name] })));
}

const ProjectsPage = page(() => import('./pages/ProjectsPage'), 'ProjectsPage');
const ProjectDetailPage = page(() => import('./pages/ProjectDetailPage'), 'ProjectDetailPage');
const SessionsPage = page(() => import('./pages/SessionsPage'), 'SessionsPage');
const SessionDetailPage = page(() => import('./pages/SessionDetailPage'), 'SessionDetailPage');
const NewRunPage = page(() => import('./pages/NewRunPage'), 'NewRunPage');
const RunsPage = page(() => import('./pages/RunsPage'), 'RunsPage');
const RunDetailPage = page(() => import('./pages/RunDetailPage'), 'RunDetailPage');
const ApprovalsPage = page(() => import('./pages/ApprovalsPage'), 'ApprovalsPage');
const BomPage = page(() => import('./pages/BomPage'), 'BomPage');
const TwinViewerPage = page(() => import('./pages/TwinViewerPage'), 'TwinViewerPage');
const FilesPage = page(() => import('./pages/FilesPage'), 'FilesPage');
const KnowledgePage = page(() => import('./pages/KnowledgePage'), 'KnowledgePage');
const SourceDetailPage = page(() => import('./pages/SourceDetailPage'), 'SourceDetailPage');
const CompliancePage = page(() => import('./pages/CompliancePage'), 'CompliancePage');
const SettingsPage = page(() => import('./pages/SettingsPage'), 'SettingsPage');

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

const ROUTES: Array<[path: string, Page: ComponentType]> = [
  ['projects', ProjectsPage],
  ['projects/:id', ProjectDetailPage],
  ['sessions', SessionsPage],
  ['sessions/:id', SessionDetailPage],
  ['runs', RunsPage],
  ['runs/new', NewRunPage],
  ['runs/:id', RunDetailPage],
  ['approvals', ApprovalsPage],
  ['bom', BomPage],
  ['twin', TwinViewerPage],
  ['files', FilesPage],
  ['knowledge', KnowledgePage],
  ['knowledge/sources/:id', SourceDetailPage],
  ['compliance', CompliancePage],
  ['settings', SettingsPage],
];

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* Inside the query client: AuthGate asks the gateway, via useHealth,
          whether a session is needed at all. */}
      <AuthProvider>
        <AuthGate>
          {/* Inside AuthGate: with no reachable gateway there is no auth_mode
              to honour, so setup has to come after that question is asked. */}
          <OnboardingGate>
            <BrowserRouter>
              <Suspense
                fallback={
                  <div className="workspace-empty" role="status">
                    Loading workspace…
                  </div>
                }
              >
                <Routes>
                  <Route element={<AppLayout />}>
                    <Route index element={<Navigate to="/projects" replace />} />
                    {ROUTES.map(([path, Page]) => (
                      <Route
                        key={path}
                        path={path}
                        element={
                          <ErrorBoundary>
                            <Page />
                          </ErrorBoundary>
                        }
                      />
                    ))}
                  </Route>
                  <Route
                    path="*"
                    element={
                      <div className="workspace-empty">
                        <h1>Page not found</h1>
                        <a className="text-action" href="/projects">
                          Return to projects
                        </a>
                      </div>
                    }
                  />
                </Routes>
              </Suspense>
              <Toaster />
            </BrowserRouter>
          </OnboardingGate>
        </AuthGate>
      </AuthProvider>
    </QueryClientProvider>
  );
}
