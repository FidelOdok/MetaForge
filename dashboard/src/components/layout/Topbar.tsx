import { TopbarChatToggle } from './TopbarChatToggle';

interface TopbarProps {
  title?: string;
}

export function Topbar({ title }: TopbarProps) {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-zinc-200 bg-white/80 px-6 backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/80">
      <div className="flex items-center gap-2">
        {title && (
          <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {title}
          </h1>
        )}
      </div>
      <div className="flex items-center gap-2">
        <TopbarChatToggle />
      </div>
    </header>
  );
}
