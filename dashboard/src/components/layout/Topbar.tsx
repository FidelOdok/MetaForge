import { useState } from 'react';
import { TopbarChatToggle } from './TopbarChatToggle';
import { ThemeToggle } from './ThemeToggle';
import { RunAgentDialog } from '../shared/RunAgentDialog';

interface TopbarProps {
  title?: string;
}

export function Topbar({ title }: TopbarProps) {
  const [runDialogOpen, setRunDialogOpen] = useState(false);

  return (
    <>
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-zinc-200 bg-white/80 px-6 backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/80">
        <div className="flex items-center gap-2">
          {title && (
            <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {title}
            </h1>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setRunDialogOpen(true)}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
          >
            Run Agent
          </button>
          <ThemeToggle />
          <TopbarChatToggle />
        </div>
      </header>

      {runDialogOpen && (
        <RunAgentDialog onClose={() => setRunDialogOpen(false)} />
      )}
    </>
  );
}
