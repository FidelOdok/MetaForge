import { useState } from 'react';
import { Card } from './ui/Card';
import { Button } from './ui/Button';
import { useNodeLink, useCreateLink, useDeleteLink, useSyncNode } from '../hooks/use-links';
import type { FileLinkTool, FileLinkStatus, SyncResult } from '../types/twin';

const TOOL_OPTIONS: { value: FileLinkTool; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'kicad', label: 'KiCad' },
  { value: 'freecad', label: 'FreeCAD' },
  { value: 'cadquery', label: 'CadQuery' },
];

function StatusDot({ status }: { status: FileLinkStatus }) {
  const colors: Record<FileLinkStatus, string> = {
    synced: 'bg-green-500',
    changed: 'bg-yellow-500',
    disconnected: 'bg-red-500',
  };
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${colors[status]}`}
      aria-hidden="true"
    />
  );
}

function StatusLabel({ status }: { status: FileLinkStatus }) {
  if (status === 'synced') {
    return (
      <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
        Synced
      </span>
    );
  }
  if (status === 'changed') {
    return (
      <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
        Changes detected
      </span>
    );
  }
  return (
    <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400">
      File missing
    </span>
  );
}

function DiffSummary({ changes }: { changes: SyncResult['changes'] }) {
  const entries = Object.entries(changes);
  if (entries.length === 0) {
    return (
      <p className="text-xs text-zinc-500">No metadata changes detected.</p>
    );
  }
  return (
    <dl className="space-y-1">
      {entries.map(([key, { before, after }]) => (
        <div key={key}>
          <dt className="text-xs font-medium text-zinc-600 dark:text-zinc-400">{key}</dt>
          <dd className="text-xs text-zinc-500">
            <span className="text-red-500 line-through">{String(before)}</span>
            {' → '}
            <span className="text-green-600">{String(after)}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

export interface LinkPanelProps {
  nodeId: string;
}

export function LinkPanel({ nodeId }: LinkPanelProps) {
  const { data: link, isLoading } = useNodeLink(nodeId);
  const createLinkMutation = useCreateLink(nodeId);
  const deleteLinkMutation = useDeleteLink(nodeId);
  const syncMutation = useSyncNode(nodeId);

  const [filePath, setFilePath] = useState('');
  const [tool, setTool] = useState<FileLinkTool>('none');
  const [watch, setWatch] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (isLoading) {
    return (
      <Card>
        <p className="text-sm text-zinc-500">Loading link status...</p>
      </Card>
    );
  }

  const handleCreate = () => {
    if (!filePath.trim()) return;
    setErrorMsg(null);
    createLinkMutation.mutate(
      { file_path: filePath.trim(), tool, watch },
      {
        onSuccess: () => {
          setFilePath('');
          setTool('none');
          setWatch(false);
        },
        onError: (err) => {
          setErrorMsg(err instanceof Error ? err.message : 'Failed to create link');
        },
      },
    );
  };

  const handleSync = () => {
    setErrorMsg(null);
    setSyncResult(null);
    syncMutation.mutate(undefined, {
      onSuccess: (result) => {
        setSyncResult(result);
      },
      onError: (err) => {
        setErrorMsg(err instanceof Error ? err.message : 'Sync failed');
      },
    });
  };

  const handleUnlink = () => {
    if (!confirmUnlink) {
      setConfirmUnlink(true);
      return;
    }
    setErrorMsg(null);
    deleteLinkMutation.mutate(undefined, {
      onSuccess: () => {
        setConfirmUnlink(false);
        setSyncResult(null);
      },
      onError: (err) => {
        setErrorMsg(err instanceof Error ? err.message : 'Failed to unlink');
        setConfirmUnlink(false);
      },
    });
  };

  return (
    <Card>
      <h4 className="mb-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
        Source File Link
      </h4>

      {errorMsg && (
        <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-400">
          {errorMsg}
        </div>
      )}

      {!link ? (
        /* No link — show create form */
        <div className="space-y-3">
          <div>
            <label htmlFor="link-path" className="mb-1 block text-xs text-zinc-500">
              File path
            </label>
            <input
              id="link-path"
              type="text"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              placeholder="/path/to/file.step"
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-900 placeholder-zinc-400 focus:border-blue-500 focus:outline-none dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
            />
          </div>

          <div>
            <label htmlFor="link-tool" className="mb-1 block text-xs text-zinc-500">
              Tool
            </label>
            <select
              id="link-tool"
              value={tool}
              onChange={(e) => setTool(e.target.value as FileLinkTool)}
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-900 focus:border-blue-500 focus:outline-none dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
            >
              {TOOL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <input
              id="link-watch"
              type="checkbox"
              checked={watch}
              onChange={(e) => setWatch(e.target.checked)}
              className="rounded border-zinc-300 text-blue-600"
            />
            <label htmlFor="link-watch" className="text-xs text-zinc-600 dark:text-zinc-400">
              Watch file for changes
            </label>
          </div>

          <Button
            variant="primary"
            size="sm"
            onClick={handleCreate}
            disabled={createLinkMutation.isPending || !filePath.trim()}
          >
            {createLinkMutation.isPending ? 'Linking...' : 'Link file'}
          </Button>
        </div>
      ) : (
        /* Has link — show status + actions */
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <StatusDot status={link.status} />
            <StatusLabel status={link.status} />
          </div>

          <div>
            <p className="text-xs text-zinc-500">File path</p>
            <p className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
              {link.file_path}
            </p>
          </div>

          <div className="flex items-center gap-3 text-xs text-zinc-500">
            <span>Tool: {link.tool}</span>
            {link.watch && <span>Watching</span>}
            {link.last_synced_at && (
              <span>Last synced: {new Date(link.last_synced_at).toLocaleString()}</span>
            )}
          </div>

          {link.status === 'disconnected' && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-400">
              Warning: the source file is missing or inaccessible.
            </p>
          )}

          <div className="flex gap-2">
            {link.status !== 'disconnected' && (
              <Button
                variant={link.status === 'changed' ? 'primary' : 'secondary'}
                size="sm"
                onClick={handleSync}
                disabled={syncMutation.isPending}
              >
                {syncMutation.isPending ? 'Syncing...' : 'Sync now'}
              </Button>
            )}

            <Button
              variant="danger"
              size="sm"
              onClick={handleUnlink}
              disabled={deleteLinkMutation.isPending}
            >
              {confirmUnlink
                ? deleteLinkMutation.isPending
                  ? 'Unlinking...'
                  : 'Confirm unlink?'
                : 'Unlink'}
            </Button>

            {confirmUnlink && !deleteLinkMutation.isPending && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmUnlink(false)}
              >
                Cancel
              </Button>
            )}
          </div>

          {syncResult && (
            <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900">
              <p className="mb-2 text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Sync summary
              </p>
              <DiffSummary changes={syncResult.changes} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
