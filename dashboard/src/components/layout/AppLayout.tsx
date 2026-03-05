import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';
import { ChatSidebar } from '../chat/ChatSidebar';

export function AppLayout() {
  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-900">
      <Sidebar />

      <div className="ml-60">
        <Topbar />
        <main className="p-6">
          <Outlet />
        </main>
      </div>

      <ChatSidebar />
    </div>
  );
}
