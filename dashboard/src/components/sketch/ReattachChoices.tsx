import { useState } from 'react';
import type { SketchPlane } from '../../api/endpoints/sketchPlanes';
import { refusalOf } from '../../api/endpoints/create';
import { useSketchPlaneActions } from '../../hooks/use-sketch-plane';
import type { Principal } from '../../lib/sketch/planeMath';
import { FaceSurfacePicker } from './FaceSurfacePicker';
import { PLANE_CHOICES } from './PlanePrompt';
import '../../styles/sketch-plane.css';

/** The plain-language line shown in Create (and Engineering) when a sketch lost its surface. */
export const REATTACH_MESSAGE = 'The surface this sketch was on changed; choose where it goes';

export interface ReattachChoicesProps {
  projectId: string;
  sketchKey: string;
  plane: SketchPlane;
  /** Engineering shows what the remap saw; Create keeps to the plain line. */
  detailed?: boolean;
  onClose?: () => void;
}

/**
 * The answer to REATTACHMENT_REQUIRED (PLN-010): pick a surface on the part's
 * current revision (the faces the remap could not choose between are marked),
 * move the sketch onto Front, Side or Top, keep it as a concept, or decide
 * later. Until then the sketch keeps its last valid place, and exact builds
 * from it are on hold.
 */
export function ReattachChoices({ projectId, sketchKey, plane, detailed, onClose }: ReattachChoicesProps) {
  const { resolve } = useSketchPlaneActions(projectId, sketchKey);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = plane.attachment?.pending ?? null;
  const candidates = (pending?.candidates ?? []).map((c) => c.face_id);

  const detach = (principal: Principal | null) => {
    setError(null);
    resolve.mutate(
      principal ? { action: 'detach_principal', principal } : { action: 'detach_unassigned' },
      { onSuccess: () => onClose?.(), onError: (err) => setError(refusalOf(err)) },
    );
  };

  return (
    <div className="sk-plane-reattach" role="group" aria-label="Choose where the sketch goes" data-testid="reattach-choices">
      <strong data-testid="reattach-message">{REATTACH_MESSAGE}.</strong>
      {detailed && pending && (
        <span className="sk-plane-note" data-testid="reattach-detail">
          {pending.outcome === 'ambiguous'
            ? `${candidates.length} surfaces of ${pending.item_ref} could be it`
            : `It is not on ${pending.item_ref}`}
          {pending.reason ? ` (${pending.reason})` : ''}. Builds from this sketch wait until you choose.
        </span>
      )}
      <div className="sk-plane-choices">
        <button type="button" className="sk-plane-choice" onClick={() => setPicking((p) => !p)} aria-expanded={picking}>
          Pick the surface
        </button>
        {PLANE_CHOICES.map((c) => (
          <button
            key={c.principal}
            type="button"
            className="sk-plane-choice"
            title={`Move the sketch off the model onto the ${c.label} plane`}
            disabled={resolve.isPending}
            onClick={() => detach(c.principal)}
          >
            {c.label}
          </button>
        ))}
        <button type="button" className="sk-plane-choice is-quiet" disabled={resolve.isPending} onClick={() => detach(null)}>
          Keep as concept
        </button>
        <button
          type="button"
          className="sk-plane-choice is-quiet"
          title="Leave it where it last was; builds from this sketch stay on hold"
          onClick={() => onClose?.()}
        >
          Decide later
        </button>
      </div>
      {picking && plane.attachment && (
        <FaceSurfacePicker
          projectId={projectId}
          sketchKey={sketchKey}
          mode="resolve"
          itemKey={plane.attachment.item_key}
          revision={pending?.revision ?? null}
          highlighted={candidates}
          onDone={(picked) => {
            setPicking(false);
            if (picked) onClose?.();
          }}
        />
      )}
      {error && (
        <p className="sk-plane-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
