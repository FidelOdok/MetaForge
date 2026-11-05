import { useState } from 'react';
import { confirmMaterial, type ChangeClassification } from '../../api/endpoints/changeClassification';
import { refusalOf } from '../../api/endpoints/create';
import { showsClassification } from '../../lib/appearance';

/**
 * One plain line under a message: what the request changes, decided before
 * anything changed (spec section 5). "Colour change only. Your engineering
 * model stays the same." A material change offers to confirm the proposed
 * material, which the person states on the manufacturing context.
 *
 * The look-or-material question for "make it metallic" arrives as the reply
 * itself, with "The look" and "The material" as its two answers.
 */
export function ChangeClassificationLine({
  projectId,
  classification,
}: {
  projectId: string;
  classification: ChangeClassification | null | undefined;
}) {
  const [state, setState] = useState<'idle' | 'saving' | 'done' | 'failed'>('idle');
  const [error, setError] = useState<string | null>(null);
  if (!showsClassification(classification)) return null;
  const proposed = classification.proposed_context_update?.selected_material;
  const material =
    classification.change_class === 'material_engineering' && typeof proposed === 'string' ? proposed : null;

  const confirm = async () => {
    if (!material) return;
    setState('saving');
    try {
      await confirmMaterial(projectId, material);
      setState('done');
    } catch (e) {
      setError(refusalOf(e));
      setState('failed');
    }
  };

  return (
    <p className={`cv-classified cv-classified-${classification.change_class}`} role="status">
      <span>{classification.message}</span>
      {material && state !== 'done' && (
        <button type="button" className="cv-link" disabled={state === 'saving'} onClick={() => void confirm()}>
          Use {material}
        </button>
      )}
      {state === 'done' && material && (
        <span className="cv-muted"> {material} confirmed. Checks that depend on it are marked for a re-run.</span>
      )}
      {state === 'failed' && <span className="cv-error"> {error ?? 'Not saved'}</span>}
    </p>
  );
}

/** The line while the reply is still coming: the plain part of the `classified` event. */
export function PendingClassificationLine({ line }: { line: string | null }) {
  if (!line) return null;
  return (
    <p className="cv-classified" role="status">
      <span>{line}</span>
    </p>
  );
}
