import { useLocation } from 'react-router-dom';
import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { ChatSidebar } from '../chat/ChatSidebar';
import { FloatingAssistantInput } from './FloatingAssistantInput';

export function AppLayout() {
  const location = useLocation();
  // Design Assistant already has a full embedded chat — don't render the pill there
  const showPill = location.pathname !== '/assistant';

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

      {/* Floating assistant input pill — present on all pages except Design Assistant */}
      {showPill && <FloatingAssistantInput />}
    </div>
  );
}
