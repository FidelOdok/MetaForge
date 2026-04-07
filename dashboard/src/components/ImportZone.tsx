import { useRef, useState, useCallback } from 'react';
import { Upload, CheckCircle, XCircle, RotateCcw } from 'lucide-react';
import { Card } from './ui/Card';
import { Button } from './ui/Button';
import { useToast } from './ui/Toast';
import { useImportWorkProduct } from '../hooks/use-import';
import type { ImportWorkProductResponse } from '../types/twin';

const ALLOWED_EXTENSIONS = ['.step', '.stp', '.iges', '.igs', '.kicad_sch', '.kicad_pcb', '.kicad_pro', '.fcstd'];
const MAX_BYTES = 100 * 1024 * 1024; // 100 MB

function getExtension(filename: string): string {
  const lastDot = filename.lastIndexOf('.');
  return lastDot >= 0 ? filename.slice(lastDot).toLowerCase() : '';
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type UploadState = 'idle' | 'uploading' | 'success' | 'error';

export interface ImportZoneProps {
  projectId?: string;
  onSuccess?: (result: ImportWorkProductResponse) => void;
}

export function ImportZone({ projectId, onSuccess }: ImportZoneProps) {
  const toast = useToast();
  const mutation = useImportWorkProduct();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [state, setState] = useState<UploadState>('idle');
  const [progress, setProgress] = useState(0);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportWorkProductResponse | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const reset = useCallback(() => {
    setState('idle');
    setProgress(0);
    setValidationError(null);
    setResult(null);
    mutation.reset();
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [mutation]);

  const validateFile = useCallback((file: File): string | null => {
    const ext = getExtension(file.name);
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      return `Unsupported file type "${ext || '(none)'}". Allowed: ${ALLOWED_EXTENSIONS.join(', ')}`;
    }
    if (file.size > MAX_BYTES) {
      return `File is too large (${formatBytes(file.size)}). Maximum size is 100 MB.`;
    }
    return null;
  }, []);

  const uploadFile = useCallback(
    (file: File) => {
      const error = validateFile(file);
      if (error) {
        setValidationError(error);
        setState('error');
        return;
      }

      setValidationError(null);
      setState('uploading');
      setProgress(0);

      const formData = new FormData();
      formData.append('file', file);
      if (projectId) formData.append('project_id', projectId);

      mutation.mutate(
        {
          formData,
          onProgress: (pct) => setProgress(pct),
        },
        {
          onSuccess: (data) => {
            setState('success');
            setResult(data);
            toast.success('Work product imported successfully');
            onSuccess?.(data);
          },
          onError: (err) => {
            setState('error');
            setValidationError(err.message ?? 'Server error. Please try again.');
          },
        },
      );
    },
    [validateFile, projectId, mutation, toast, onSuccess],
  );

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadFile(file);
    },
    [uploadFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) uploadFile(file);
    },
    [uploadFile],
  );

  // ── Success state ──────────────────────────────────────────────────────────
  if (state === 'success' && result) {
    const metaEntries = Object.entries(result.metadata).slice(0, 5);
    return (
      <Card data-testid="import-success-card">
        <div className="mb-3 flex items-center gap-2">
          <CheckCircle size={18} className="text-green-500" />
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Import successful
          </span>
        </div>

        <div className="mb-3 flex flex-wrap gap-2">
          <span className="inline-flex items-center rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/40 dark:text-blue-300">
            {result.domain}
          </span>
          <span className="inline-flex items-center rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs font-medium text-zinc-700 dark:bg-zinc-700 dark:text-zinc-300">
            {result.wp_type}
          </span>
          <span className="inline-flex items-center rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs font-medium text-zinc-700 dark:bg-zinc-700 dark:text-zinc-300">
            .{result.format}
          </span>
        </div>

        <p className="mb-2 text-sm font-medium text-zinc-800 dark:text-zinc-200">{result.name}</p>

        {metaEntries.length > 0 && (
          <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md bg-zinc-50 p-3 dark:bg-zinc-900/50">
            {metaEntries.map(([key, value]) => (
              <div key={key}>
                <dt className="text-xs text-zinc-500">{key}</dt>
                <dd className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-200">
                  {String(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}

        <Button variant="secondary" size="sm" onClick={reset}>
          <RotateCcw size={13} className="mr-1.5" />
          Import another
        </Button>
      </Card>
    );
  }

  // ── Uploading state ────────────────────────────────────────────────────────
  if (state === 'uploading') {
    return (
      <Card data-testid="import-uploading">
        <p className="mb-2 text-sm text-zinc-600 dark:text-zinc-400">Uploading…</p>
        <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div
            data-testid="progress-bar"
            className="h-full rounded-full bg-blue-500 transition-all duration-150"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="mt-1 text-right text-xs text-zinc-400">{progress}%</p>
      </Card>
    );
  }

  // ── Idle / Error state ─────────────────────────────────────────────────────
  return (
    <div data-testid="import-zone">
      <div
        role="button"
        tabIndex={0}
        aria-label="Drop a file here or click to browse"
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 ${
          dragOver
            ? 'border-blue-400 bg-blue-50 dark:border-blue-500 dark:bg-blue-900/20'
            : 'border-zinc-300 hover:border-zinc-400 hover:bg-zinc-50 dark:border-zinc-600 dark:hover:border-zinc-500 dark:hover:bg-zinc-800/50'
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
        }}
      >
        <Upload
          size={28}
          className={`mb-3 ${dragOver ? 'text-blue-500' : 'text-zinc-400'}`}
        />
        <p className="mb-1 text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Drag &amp; drop a file here, or click to browse
        </p>
        <p className="text-xs text-zinc-400">
          {ALLOWED_EXTENSIONS.join(', ')} &middot; max 100 MB
        </p>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept={ALLOWED_EXTENSIONS.join(',')}
        className="hidden"
        onChange={handleFileInput}
        data-testid="file-input"
      />

      {state === 'error' && validationError && (
        <div
          role="alert"
          data-testid="import-error"
          className="mt-2 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-300"
        >
          <XCircle size={15} className="mt-0.5 flex-shrink-0" />
          <span>{validationError}</span>
        </div>
      )}
    </div>
  );
}
