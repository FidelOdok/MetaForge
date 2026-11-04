import { Suspense, lazy, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { getNodeModel } from '../../api/endpoints/twin';
import { refusalOf } from '../../api/endpoints/create';
import { useAttachableGeometry, useCandidateFaces, useSketchPlaneActions } from '../../hooks/use-sketch-plane';
import { faceLabel } from '../../lib/sketch/faceMesh';
import { resolveGatewayHref } from '../../lib/gatewayConfig';
import '../../styles/sketch-plane.css';

const FacePickCanvas = lazy(() => import('./FacePickCanvas'));

export interface FaceSurfacePickerProps {
  projectId: string;
  sketchKey: string;
  /** `attach`: put the sketch on a face. `resolve`: answer REATTACHMENT_REQUIRED. */
  mode?: 'attach' | 'resolve';
  /** The CAD item to pick on (resolve: the one the sketch was on). Chosen by the person otherwise. */
  itemKey?: string | null;
  /** The revision to pick on; the head when not given. */
  revision?: number | null;
  /** Faces to mark as candidates (a remap that could not choose between them). */
  highlighted?: string[];
  /** `true` once a face was picked and saved; `false` when closed. */
  onDone: (picked: boolean) => void;
}

/**
 * Pick a planar face of a validated CAD revision for a sketch (CDE slice 9).
 *
 * The model shows the revision's converted GLB; planar faces light up under
 * the pointer (through the face-to-triangle map) and a click picks. The same
 * faces are listed as buttons beside it, so picking works by keyboard and
 * without WebGL. Nothing is attached until the person picks.
 */
export function FaceSurfacePicker({ projectId, sketchKey, mode = 'attach', itemKey, revision, highlighted = [], onDone }: FaceSurfacePickerProps) {
  const attachable = useAttachableGeometry(projectId, !itemKey);
  const [chosen, setChosen] = useState<string | null>(itemKey ?? null);
  useEffect(() => {
    const first = attachable.data?.items[0];
    if (!chosen && first) setChosen(first.item_key);
  }, [chosen, attachable.data]);
  const faces = useCandidateFaces(projectId, chosen, revision ?? null);
  const nodeId = faces.data?.node_id ?? null;
  const model = useQuery({
    queryKey: ['sketch-plane', 'face-model', nodeId],
    queryFn: () => getNodeModel(nodeId!),
    enabled: !!nodeId,
    staleTime: 300_000,
    retry: false,
  });
  const { attachFace, resolve } = useSketchPlaneActions(projectId, sketchKey);
  const [hovered, setHovered] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = attachFace.isPending || resolve.isPending;

  const pick = (faceId: string) => {
    if (!faces.data || pending) return;
    setError(null);
    const done = { onSuccess: () => onDone(true), onError: (err: unknown) => setError(refusalOf(err)) };
    if (mode === 'resolve') {
      resolve.mutate({ action: 'pick_face', face_id: faceId, revision: faces.data.revision }, done);
    } else {
      attachFace.mutate({ item_key: faces.data.item_key, revision: faces.data.revision, face_id: faceId }, done);
    }
  };

  const items = attachable.data?.items ?? [];
  const candidates = new Set(highlighted);
  const glbUrl = model.data?.glb_url ? resolveGatewayHref(model.data.glb_url) : null;

  return (
    <div className="sk-face-picker" role="group" aria-label="Pick a surface for this sketch" data-testid="face-picker">
      <div className="sk-plane-head">
        <span>{mode === 'resolve' ? 'Choose the surface this sketch goes on' : 'Pick a flat surface of the model'}</span>
        <button type="button" className="sk-plane-close" aria-label="Close" onClick={() => onDone(false)}>
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      {!itemKey && items.length > 1 && (
        <select aria-label="Part" value={chosen ?? ''} onChange={(e) => setChosen(e.target.value)}>
          {items.map((i) => (
            <option key={i.item_key} value={i.item_key}>
              {i.name} ({i.item_ref})
            </option>
          ))}
        </select>
      )}
      {!itemKey && attachable.data && items.length === 0 && (
        <p className="sk-plane-note" data-testid="face-picker-empty">
          {attachable.data.reason}
        </p>
      )}
      {faces.isError && (
        <p className="sk-plane-error" role="alert">
          {refusalOf(faces.error)}
        </p>
      )}
      {faces.data && (
        <>
          <p className="sk-plane-note">
            {faces.data.item_ref}: {faces.data.faces.length} flat surface{faces.data.faces.length === 1 ? '' : 's'}
            {candidates.size ? ' · the marked ones could be where it was' : ''}
          </p>
          {glbUrl && (
            <Suspense fallback={null}>
              <div className="sk-face-view">
                <FacePickCanvas
                  glbUrl={glbUrl}
                  faces={faces.data.faces}
                  partSize={faces.data.part_size}
                  hovered={hovered}
                  highlighted={highlighted}
                  onHover={setHovered}
                  onPick={pick}
                />
              </div>
            </Suspense>
          )}
          <ul className="sk-face-list" aria-label="Flat surfaces">
            {faces.data.faces.map((f) => (
              <li key={f.face_id}>
                <button
                  type="button"
                  data-testid={`face-option-${f.face_id}`}
                  className={`sk-face-option${hovered === f.face_id ? ' is-hovered' : ''}${candidates.has(f.face_id) ? ' is-candidate' : ''}`}
                  aria-pressed={hovered === f.face_id}
                  title={f.signature_summary}
                  disabled={pending}
                  onMouseEnter={() => setHovered(f.face_id)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(f.face_id)}
                  onClick={() => pick(f.face_id)}
                >
                  {faceLabel(f)} <span className="sk-face-area">{f.area.toPrecision(3)} mm²</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {error && (
        <p className="sk-plane-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
