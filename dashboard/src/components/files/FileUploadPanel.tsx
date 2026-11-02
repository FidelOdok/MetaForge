import { useCallback, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createUpload,
  listUploads,
  type PathedFile,
  type StagedUpload,
} from '../../api/endpoints/uploads';
import { Button } from '../ui/Button';

/** What the gateway imports; anything else is listed as left out, not dropped here. */
export const UPLOAD_ACCEPT = [
  '.step', '.stp', '.iges', '.igs', '.fcstd',
  '.stl', '.obj', '.glb', '.gltf', '.ply', '.3mf',
  '.gcode', '.gco', '.bgcode',
  '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg',
  '.kicad_sch', '.kicad_pcb', '.kicad_pro', '.gbr', '.drl', '.xln',
  '.c', '.h', '.cpp', '.hpp', '.hex', '.map',
  '.md', '.txt', '.pdf', '.json', '.csv', '.zip',
];

const STATUS_COLOR: Record<StagedUpload['status'], string> = {
  open: 'var(--mf-c-9a9aaa)',
  pending: 'var(--mf-c-f59e0b)',
  approved: 'var(--mf-c-3dd68c)',
  rejected: 'var(--mf-c-ffb4ab)',
  expired: 'var(--mf-c-9a9aaa)',
};

const SKIP = new Set(['.git', 'node_modules', '__pycache__', '.venv', '.DS_Store', '__MACOSX']);

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Every file under a dropped entry, with its path; a dropped folder keeps its name. */
async function walk(entry: FileSystemEntry, prefix: string, out: PathedFile[]): Promise<void> {
  if (SKIP.has(entry.name)) return;
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((ok, err) => (entry as FileSystemFileEntry).file(ok, err));
    out.push({ file, path });
    return;
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries returns a batch at a time; an empty batch means done.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((ok, err) => reader.readEntries(ok, err));
      if (batch.length === 0) break;
      for (const child of batch) await walk(child, path, out);
    }
  }
}

async function filesFromDrop(e: React.DragEvent): Promise<Array<File | PathedFile>> {
  const entries = Array.from(e.dataTransfer.items ?? [])
    .map((item) => item.webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is FileSystemEntry => entry !== null);
  if (entries.length === 0) return Array.from(e.dataTransfer.files);
  const out: PathedFile[] = [];
  for (const entry of entries) await walk(entry, '', out);
  return out;
}

export interface FileUploadPanelProps {
  projectId?: string | null;
}

/**
 * Bring any files or folders into the twin: CAD, meshes, G-code, images,
 * KiCad, firmware and documents. "Import now" makes the person's choice the
 * approval; otherwise the upload waits on the Approvals page.
 */
export function FileUploadPanel({ projectId }: FileUploadPanelProps) {
  const queryClient = useQueryClient();
  const filesRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [importNow, setImportNow] = useState(true);
  const [description, setDescription] = useState('');
  const [last, setLast] = useState<StagedUpload | null>(null);

  const uploads = useQuery({
    queryKey: ['uploads', projectId ?? 'all'],
    queryFn: () => listUploads({ projectId: projectId ?? undefined }),
    refetchInterval: 15_000,
  });

  const upload = useMutation({
    mutationFn: (files: Array<File | PathedFile>) =>
      createUpload(files, { projectId, description, approve: importNow }),
    onSuccess: (result) => {
      setLast(result);
      setDescription('');
      queryClient.invalidateQueries({ queryKey: ['uploads'] });
      queryClient.invalidateQueries({ queryKey: ['approvals'] });
      queryClient.invalidateQueries({ queryKey: ['twin'] });
    },
  });

  const send = useCallback(
    (files: Array<File | PathedFile>) => {
      if (files.length > 0) upload.mutate(files);
    },
    [upload],
  );

  const errorText =
    (upload.error as { response?: { data?: { detail?: string } } } | null)?.response?.data?.detail ??
    upload.error?.message;

  return (
    <section data-testid="file-upload-panel" style={{ marginBottom: 16 }}>
      <div
        role="button"
        tabIndex={0}
        aria-label="Drop files or folders here, or choose them"
        data-testid="upload-drop"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void filesFromDrop(e).then(send);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') filesRef.current?.click();
        }}
        style={{
          border: `2px dashed ${dragOver ? '#ff5a0a' : 'var(--mf-r-65-72-90-0p2)'}`,
          borderRadius: 6,
          padding: '20px 16px',
          textAlign: 'center',
          background: 'var(--mf-r-30-31-38-0p85)',
        }}
      >
        <div style={{ fontSize: 13, color: 'var(--mf-c-e8e8ed)', marginBottom: 4 }}>
          {upload.isPending ? 'Uploading…' : 'Drop files or folders here'}
        </div>
        <div style={{ fontSize: 11, color: 'var(--mf-c-9a9aaa)', marginBottom: 10 }}>
          CAD, meshes, G-code, images, KiCad, firmware, PDFs and spreadsheets · 100 MB a file
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
          <Button variant="secondary" size="sm" onClick={() => filesRef.current?.click()}>
            Choose files
          </Button>
          <Button variant="secondary" size="sm" onClick={() => folderRef.current?.click()}>
            Choose a folder
          </Button>
        </div>
        <div
          style={{
            display: 'flex',
            gap: 12,
            justifyContent: 'center',
            alignItems: 'center',
            flexWrap: 'wrap',
            marginTop: 10,
            fontSize: 12,
            color: 'var(--mf-c-9a9aaa)',
          }}
        >
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={importNow}
              onChange={(e) => setImportNow(e.target.checked)}
              data-testid="import-now"
            />
            Import now (otherwise it waits on the Approvals page)
          </label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What are these files? (optional)"
            aria-label="Description"
            style={{
              minWidth: 220,
              background: 'transparent',
              border: '1px solid var(--mf-r-65-72-90-0p2)',
              borderRadius: 4,
              padding: '3px 8px',
              color: 'var(--mf-c-e8e8ed)',
            }}
          />
        </div>
        <input
          ref={filesRef}
          type="file"
          multiple
          accept={UPLOAD_ACCEPT.join(',')}
          style={{ display: 'none' }}
          data-testid="upload-files-input"
          onChange={(e) => send(Array.from(e.target.files ?? []))}
        />
        <input
          ref={folderRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          data-testid="upload-folder-input"
          {...({ webkitdirectory: '' } as Record<string, string>)}
          onChange={(e) => send(Array.from(e.target.files ?? []))}
        />
      </div>

      {upload.isError && (
        <p role="alert" style={{ color: 'var(--mf-c-ffb4ab)', fontSize: 12, marginTop: 8 }}>
          {errorText}
        </p>
      )}

      {last && <UploadResult upload={last} />}

      <RecentUploads uploads={uploads.data ?? []} />
    </section>
  );
}

function UploadResult({ upload }: { upload: StagedUpload }) {
  const left = upload.files.filter((f) => f.error && !f.work_product_id);
  const done = upload.files.filter((f) => f.work_product_id);
  return (
    <div data-testid="upload-result" style={{ marginTop: 10, fontSize: 12 }}>
      {upload.status === 'approved' ? (
        <p style={{ color: 'var(--mf-c-3dd68c)', margin: '0 0 4px' }}>
          {done.length} file(s) imported into the twin.
        </p>
      ) : (
        <p style={{ color: 'var(--mf-c-f59e0b)', margin: '0 0 4px' }}>
          {upload.files.length - left.length} file(s) are waiting for approval.{' '}
          <Link to="/approvals" style={{ color: '#ff5a0a' }}>
            Review on the Approvals page
          </Link>
        </p>
      )}
      {left.map((f) => (
        <p key={f.name} style={{ color: 'var(--mf-c-ffb4ab)', margin: 0, fontFamily: 'monospace' }}>
          {f.name}: {f.error}
        </p>
      ))}
    </div>
  );
}

function RecentUploads({ uploads }: { uploads: StagedUpload[] }) {
  if (uploads.length === 0) return null;
  return (
    <div data-testid="recent-uploads" style={{ marginTop: 12 }}>
      <div
        style={{
          fontFamily: 'monospace',
          fontSize: 10,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--mf-c-9a9aaa)',
          marginBottom: 6,
        }}
      >
        Recent uploads
      </div>
      {uploads.slice(0, 8).map((u) => {
        const size = u.files.reduce((n, f) => n + (f.error ? 0 : f.size), 0);
        return (
          <div
            key={u.id}
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              fontSize: 12,
              padding: '4px 0',
              borderBottom: '1px solid var(--mf-r-65-72-90-0p2)',
            }}
          >
            <span style={{ color: STATUS_COLOR[u.status], fontFamily: 'monospace', minWidth: 70 }}>
              {u.status}
            </span>
            <span style={{ color: 'var(--mf-c-e8e8ed)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {u.files.map((f) => f.name).join(', ') || 'no files yet'}
            </span>
            <span style={{ color: 'var(--mf-c-9a9aaa)', fontFamily: 'monospace' }}>
              {u.source} · {formatBytes(size)}
            </span>
            {u.status === 'pending' && (
              <Link to="/approvals" style={{ color: '#ff5a0a' }}>
                review
              </Link>
            )}
          </div>
        );
      })}
    </div>
  );
}
