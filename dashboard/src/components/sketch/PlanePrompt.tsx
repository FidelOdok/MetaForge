import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { proposeSketchPlane, type PlaneProposal, type ViewHint } from '../../api/endpoints/sketchPlanes';
import { refusalOf } from '../../api/endpoints/create';
import { useAttachableGeometry, useSketchPlaneActions } from '../../hooks/use-sketch-plane';
import { VIEW_NAMES, type Principal } from '../../lib/sketch/planeMath';
import { FaceSurfacePicker } from './FaceSurfacePicker';
import '../../styles/sketch-plane.css';

/** The three principal choices, in the order the prompt shows them. */
export const PLANE_CHOICES: { principal: Principal; label: string; hint: string }[] = [
  { principal: 'XZ', label: 'Front', hint: 'Seen from the front (the XZ plane)' },
  { principal: 'YZ', label: 'Side', hint: 'Seen from the side (the YZ plane)' },
  { principal: 'XY', label: 'Top', hint: 'Seen from above (the XY plane)' },
];

/** Why "Select surface" is off when the project has no validated exact geometry. */
export const FACE_PICK_UNAVAILABLE =
  'There is no validated exact geometry to put the sketch on yet. Build and validate a part first, or choose Front, Side or Top.';

export interface PlanePromptProps {
  projectId: string;
  sketchKey: string;
  /** The client's active orthographic view, when there is one. A hint only. */
  viewHint?: ViewHint | null;
  ownerComponent?: string | null;
  /** Called once a plane is confirmed, or the person keeps the sketch as a concept. */
  onDone: (outcome: 'confirmed' | 'concept') => void;
  /** In flow inside another panel, rather than floating over the stage. */
  inline?: boolean;
}

/**
 * The compact plane choice (UXS-006, PLN-003): Front, Side, Top, Select
 * surface, Keep as concept. Shown only when a sketch needs a physical
 * meaning, never before drawing. Non-modal: the stage and the sketch stay
 * usable around it. A proposal may highlight a choice, but nothing is placed
 * until the person picks one, and an ambiguous proposal says so.
 */
export function PlanePrompt({ projectId, sketchKey, viewHint, ownerComponent, onDone, inline }: PlanePromptProps) {
  const [proposal, setProposal] = useState<PlaneProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { confirm } = useSketchPlaneActions(projectId, sketchKey);
  // CDE slice 9: a face can be picked only on validated exact geometry.
  const attachable = useAttachableGeometry(projectId);
  const canPickSurface = (attachable.data?.items.length ?? 0) > 0;
  const surfaceReason = canPickSurface
    ? 'Pick a flat surface of the model to sketch on'
    : (attachable.data?.reason ?? FACE_PICK_UNAVAILABLE);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    let live = true;
    proposeSketchPlane(projectId, sketchKey, { view_hint: viewHint ?? null, owner_component: ownerComponent ?? null })
      .then((p) => live && setProposal(p))
      .catch(() => live && setProposal(null));
    return () => {
      live = false;
    };
  }, [projectId, sketchKey, viewHint, ownerComponent]);

  const suggested = proposal && !proposal.ambiguous ? proposal.candidate.principal : null;

  const choose = (principal: Principal) => {
    setError(null);
    const accepted = proposal && principal === proposal.candidate.principal && !proposal.ambiguous ? proposal.candidate : null;
    confirm.mutate(
      {
        principal,
        owner_component: ownerComponent ?? null,
        proposed_by: accepted?.proposed_by ?? 'user',
        confidence: accepted ? accepted.confidence : null,
      },
      {
        onSuccess: () => onDone('confirmed'),
        onError: (err) => setError(refusalOf(err)),
      },
    );
  };

  return (
    <div className="sk-plane-prompt" style={inline ? { position: 'static' } : undefined} role="group" aria-label="Choose a plane for this sketch" data-testid="plane-prompt">
      <div className="sk-plane-head">
        <span>Where does this sketch sit?</span>
        <button type="button" className="sk-plane-close" aria-label="Close" onClick={() => onDone('concept')}>
          <X size={12} aria-hidden="true" />
        </button>
      </div>
      {proposal && (
        <p className="sk-plane-note" data-testid="plane-proposal-note">
          {proposal.ambiguous
            ? 'Not sure which plane fits. Please choose one.'
            : proposal.current
              ? `On the ${VIEW_NAMES[proposal.current.principal ?? 'XY']} plane now.`
              : `Suggested: ${VIEW_NAMES[proposal.candidate.principal ?? 'XY']} (${proposal.candidates[0]?.reason ?? 'a guess'}).`}
        </p>
      )}
      <div className="sk-plane-choices">
        {PLANE_CHOICES.map((c) => (
          <button
            key={c.principal}
            type="button"
            className={`sk-plane-choice${suggested === c.principal ? ' is-suggested' : ''}`}
            title={c.hint}
            disabled={confirm.isPending}
            onClick={() => choose(c.principal)}
          >
            {c.label}
          </button>
        ))}
        <button
          type="button"
          className="sk-plane-choice"
          disabled={!canPickSurface}
          aria-disabled={!canPickSurface}
          aria-expanded={picking}
          title={surfaceReason}
          data-testid="select-surface"
          onClick={() => setPicking((p) => !p)}
        >
          Select surface
        </button>
        <button type="button" className="sk-plane-choice is-quiet" onClick={() => onDone('concept')}>
          Keep as concept
        </button>
      </div>
      {!canPickSurface && attachable.data && (
        <p className="sk-plane-note" data-testid="select-surface-reason">
          Select surface: {surfaceReason}
        </p>
      )}
      {picking && canPickSurface && (
        <FaceSurfacePicker
          projectId={projectId}
          sketchKey={sketchKey}
          onDone={(picked) => {
            setPicking(false);
            if (picked) onDone('confirmed');
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
