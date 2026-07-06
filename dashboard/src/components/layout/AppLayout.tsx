import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

/**
 * App shell. General chat lives on the /assistant page (the main chat);
 * contextual chat lives in the per-entity panels on their own pages. The old
 * floating pill + right chat sidebar were retired as redundant entry points.
 */
export function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-surface text-on-surface">
      {/* 48px icon-only nav rail */}
      <Sidebar />

      {/* Main content — offset by 48px nav rail */}
      <div className="flex flex-1 flex-col overflow-hidden ml-12">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
