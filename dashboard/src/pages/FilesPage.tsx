import { useAllLinks, useDeleteLink, useSyncNode } from '../hooks/use-links';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import type { FileLink, FileLinkStatus } from '../types/twin';

function LinkStatusBadge({ status }: { status: FileLinkStatus }) {
  if (status === 'synced') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
        <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
        Synced
      </span>
    );
  }
  if (status === 'changed') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-medium text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
        <span className="h-1.5 w-1.5 rounded-full bg-yellow-500" />
        Changed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/30 dark:text-red-400">
      <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
      Disconnected
    </span>
  );
}

function LinkRow({ link }: { link: FileLink }) {
  const deleteLinkMutation = useDeleteLink(link.node_id);
  const syncMutation = useSyncNode(link.node_id);

  return (
    <tr className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
      <td className="px-4 py-3 text-xs font-mono text-zinc-500" title={link.node_id}>
        {link.node_id.slice(0, 12)}…
      </td>
      <td className="max-w-xs px-4 py-3 text-sm text-zinc-900 dark:text-zinc-100">
        <span className="block truncate" title={link.file_path}>
          {link.file_path}
        </span>
      </td>
      <td className="px-4 py-3 text-xs capitalize text-zinc-600 dark:text-zinc-400">
        {link.tool}
      </td>
      <td className="px-4 py-3">
        <LinkStatusBadge status={link.status} />
      </td>
      <td className="px-4 py-3 text-xs text-zinc-500">
        {link.last_synced_at
          ? new Date(link.last_synced_at).toLocaleString()
          : '—'}
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          {link.status !== 'disconnected' && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
            >
              {syncMutation.isPending ? 'Syncing…' : 'Sync'}
            </Button>
          )}
          <Button
            variant="danger"
            size="sm"
            onClick={() => deleteLinkMutation.mutate()}
            disabled={deleteLinkMutation.isPending}
          >
            {deleteLinkMutation.isPending ? 'Removing…' : 'Unlink'}
          </Button>
        </div>
      </td>
    </tr>
  );
}

export function FilesPage() {
  const { data: links, isLoading } = useAllLinks();

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
          Source File Links
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Manage connections between work products and local source files.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-zinc-500">Loading links...</p>
      ) : !links || links.length === 0 ? (
        <Card className="flex min-h-[200px] items-center justify-center">
          <div className="text-center">
            <p className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              No source files linked yet
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              Open a Digital Twin node and use the Link Panel to connect a source file.
            </p>
          </div>
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/50">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    Node ID
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    File Path
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    Tool
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    Status
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    Last Synced
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {links.map((link) => (
                  <LinkRow key={link.id} link={link} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
