import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { ChatSidebar } from '../chat/ChatSidebar';

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

      <ChatSidebar />
    </div>
  );
}
