import { Suspense, lazy, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  dfmKeys,
  dfmReportMarkdownPath,
  generateDfmReport,
  getReleaseChecklist,
  itemRef,
  recordVerificationEvidence,
  type DfmReportResponse,
  type DfmVerificationItem,
} from '../../api/endpoints/dfmFindings';
import { getCandidateFaces } from '../../api/endpoints/sketchPlanes';
import { getNodeModel } from '../../api/endpoints/twin';
import { resolveGatewayHref } from '../../lib/gatewayConfig';

const FacePickCanvas = lazy(() => import('../sketch/FacePickCanvas'));

/**
 * The faces an exact finding is anchored to, highlighted on the validated
 * model (C3D slice 6). Flat faces are drawn from the same face records the
 * sketch planes use; any other face (a hole, a curved overhang) is listed by
 * its id, never guessed onto the mesh.
 */
export function FaceHighlight({ projectId, geometryRef, faces }: { projectId: string; geometryRef: string; faces: string[] }) {
  const ref = itemRef(geometryRef);
  const list = useQuery({
    queryKey: ['dfm', projectId, 'faces', geometryRef],
    queryFn: ({ signal }) => getCandidateFaces(projectId, ref!.itemKey, ref!.revision, signal),
    enabled: !!ref,
    staleTime: 300_000,
    retry: false,
  });
  const nodeId = list.data?.node_id ?? null;
  const model = useQuery({
    queryKey: ['dfm', 'face-model', nodeId],
    queryFn: () => getNodeModel(nodeId!),
    enabled: !!nodeId,
    staleTime: 300_000,
    retry: false,
  });
  if (!ref) return <p className="tw-dfm-numbers">No exact revision to show the faces on.</p>;
  if (list.isLoading) return <p className="tw-dfm-numbers">Loading the model's faces…</p>;
  if (list.isError || !list.data) return <p className="tw-dfm-numbers">The model's faces could not be read.</p>;
  const known = new Set(list.data.faces.map((f) => f.face_id));
  const shown = faces.filter((f) => known.has(f));
  const other = faces.filter((f) => !known.has(f));
  const glbUrl = model.data?.glb_url ? resolveGatewayHref(model.data.glb_url) : null;
  return (
    <div className="tw-dfm-faces" data-testid="dfm-face-highlight">
      <p className="tw-dfm-numbers">
        {geometryRef}: {shown.length} flat face{shown.length === 1 ? '' : 's'} marked
        {other.length ? `; also ${other.join(', ')} (not flat, listed only)` : ''}
      </p>
      {glbUrl && shown.length > 0 && (
        <Suspense fallback={null}>
          <div className="tw-dfm-face-view">
            <FacePickCanvas
              glbUrl={glbUrl}
              faces={list.data.faces}
              partSize={list.data.part_size}
              hovered={null}
              highlighted={shown}
              onHover={() => undefined}
              onPick={() => undefined}
            />
          </div>
        </Suspense>
      )}
    </div>
  );
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? '');
      resolve(text.includes(',') ? text.slice(text.indexOf(',') + 1) : text);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function VerificationRow({ projectId, item }: { projectId: string; item: DfmVerificationItem }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const out = await recordVerificationEvidence(projectId, item.id, {
        note,
        attachment_name: file?.name ?? null,
        attachment_base64: file ? await readBase64(file) : null,
      });
      setMessage(out.message);
      setNote('');
      setFile(null);
      await qc.invalidateQueries({ queryKey: ['dfm', projectId] });
    } catch {
      setMessage('The evidence could not be recorded.');
    } finally {
      setBusy(false);
    }
  };
  const pending = item.evidence.find((e) => e.state === 'awaiting_approval');
  return (
    <li className={`tw-dfm-verification is-${item.status}`} data-testid={`dfm-verification-${item.rule_id}`}>
      <div className="tw-dfm-finding-head">
        <span className="tw-dfm-severity">{item.status.replace('_', ' ')}</span>
        <span>{item.title}</span>
      </div>
      <p>Needed: {item.needed}</p>
      <p className="tw-dfm-numbers">Why: {item.reason}</p>
      {item.status === 'satisfied' && item.satisfied_by && (
        <p className="tw-dfm-numbers">
          Approved by {item.satisfied_by.by || 'a reviewer'} for {item.satisfied_by.geometry_ref}
        </p>
      )}
      {item.status === 'awaiting_approval' && pending && (
        <p className="tw-dfm-numbers">Evidence recorded; waiting for a reviewer's approval ({pending.approval_id}).</p>
      )}
      {(item.status === 'open' || item.status === 'rejected') && (
        <div className="tw-dfm-evidence-form">
          <label>
            Evidence note
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </label>
          <input
            type="file"
            aria-label="Attach evidence"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button type="button" onClick={() => void submit()} disabled={busy || (!note.trim() && !file)}>
            {busy ? 'Recording…' : 'Record evidence for approval'}
          </button>
        </div>
      )}
      {message && <p className="tw-dfm-numbers">{message}</p>}
    </li>
  );
}

/** Physical or supplier verification: never satisfied without a person's evidence and an approval. */
export function VerificationItems({ projectId, items }: { projectId: string; items: DfmVerificationItem[] }) {
  if (!items.length) return null;
  return (
    <div className="tw-dfm-verifications" data-testid="dfm-verification-items">
      <strong>Physical verification</strong>
      <ul className="tw-dfm-findings">
        {items.map((item) => (
          <VerificationRow key={item.id} projectId={projectId} item={item} />
        ))}
      </ul>
    </div>
  );
}

const MARK: Record<string, string> = { pass: 'Resolved', fail: 'Open', not_applicable: 'Not applicable' };

/**
 * The manufacturing release checklist and the DFM report (C3D slice 6).
 *
 * The release is refused while any item is open; each open item names what
 * is missing. The report is generated on request, stored with the revision it
 * read, and opens as Markdown.
 */
export function ReleaseChecklistPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const checklist = useQuery({
    queryKey: dfmKeys.checklist(projectId),
    queryFn: ({ signal }) => getReleaseChecklist(projectId, signal),
  });
  const [report, setReport] = useState<DfmReportResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await generateDfmReport(projectId));
      await qc.invalidateQueries({ queryKey: dfmKeys.checklist(projectId) });
    } catch {
      setError('The DFM report could not be generated.');
    } finally {
      setBusy(false);
    }
  };
  if (checklist.isLoading || !checklist.data) return null;
  const data = checklist.data;
  return (
    <section className="tw-dfm-checklist" data-testid="dfm-release-checklist">
      <header>
        <strong>Manufacturing release checklist</strong>
        <span className={`tw-dfm-status is-${data.readiness.level}`}>{data.readiness.label}</span>
      </header>
      {!data.applies ? (
        <p className="tw-dfm-none">Does not apply yet: no process with exact manufacturing checks is selected.</p>
      ) : (
        <ul className="tw-dfm-checklist-items">
          {(data.items ?? []).map((item) => (
            <li key={item.id} className={`is-${item.status}`} data-testid={`checklist-${item.id}`}>
              <span className="tw-dfm-severity">{MARK[item.status]}</span> {item.label}
              <p className="tw-dfm-numbers">{item.detail}</p>
            </li>
          ))}
        </ul>
      )}
      <div className="tw-dfm-top">
        <button type="button" onClick={() => void generate()} disabled={busy}>
          {busy ? 'Generating…' : 'Generate DFM report'}
        </button>
        {report && (
          <a href={dfmReportMarkdownPath(projectId)} target="_blank" rel="noreferrer" data-testid="dfm-report-link">
            Open DFM report ({report.report.product_revision.key}, {report.report.status_label})
          </a>
        )}
      </div>
      {error && <p className="tw-dfm-none">{error}</p>}
    </section>
  );
}
