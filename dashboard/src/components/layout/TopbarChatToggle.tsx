import { useChatStore } from '@/store/chat-store';

export function TopbarChatToggle() {
  const { sidebarOpen, openSidebar, closeSidebar } = useChatStore();

  return (
    <button
      type="button"
      onClick={() => (sidebarOpen ? closeSidebar() : openSidebar())}
      aria-label={sidebarOpen ? 'Close chat' : 'Open chat'}
      aria-pressed={sidebarOpen}
      className="relative flex h-8 w-8 items-center justify-center rounded transition-colors"
      style={{
        color: sidebarOpen ? '#e67e22' : '#9a9aaa',
        background: sidebarOpen ? 'rgba(230,126,34,0.12)' : 'transparent',
      }}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-4 w-4"
      >
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    </button>
  );
}
