import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  useDesignFlows,
  useSaveFlowVersion,
  useValidateFlowEdit,
} from '../hooks/use-design-flows';
import type { DesignFlowPhase, FlowVersion } from '../types/design-flows';

/**
 * Flow editor (FORGE-399).
 *
 * Edit a template's phases, see the invariants fail while you are looking at
 * the change that broke them, then save — which creates a new immutable
 * version held for approval rather than modifying anything.
 *
 * Two deliberate absences:
 *
 * - **No local validity logic.** The rules live on the server and the editor
 *   asks. A second copy here would be a second answer to "is this flow
 *   valid", and the copy that disagrees is the one that lets an unstartable
 *   flow through the UI and get refused at save.
 * - **Nothing here starts a run.** Saving produces a held approval. The run
 *   comes later, from the approved version.
 */
export function FlowEditorPage() {
  const [searchParams] = useSearchParams();
  const catalog = useDesignFlows();
  const validate = useValidateFlowEdit();
  const save = useSaveFlowVersion();

  const flows = catalog.data?.flows ?? [];
  const [templateId, setTemplateId] = useState(searchParams.get('flow') ?? '');
  const selectedId = templateId || catalog.data?.defaultFlowId || '';
  const template = flows.find((f) => f.id === selectedId);

  const [phases, setPhases] = useState<DesignFlowPhase[]>([]);
  const [saved, setSaved] = useState<FlowVersion | null>(null);

  // Load the template into the editor when the selection changes. Edits are
  // discarded on purpose: carrying them onto a different template would
  // produce a flow that is neither.
  useEffect(() => {
    setPhases(template ? template.phases.map((p) => ({ ...p })) : []);
    setSaved(null);
    save.reset();
  }, [template?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => {
    if (!template) return false;
    return JSON.stringify(phases) !== JSON.stringify(template.phases);
  }, [phases, template]);

  // Ask the server whenever the flow changes. Debounced, because an edit is a
  // keystroke and the rules are not worth a request per character.
  useEffect(() => {
    if (!template || !dirty) return;
    const handle = setTimeout(() => {
      validate.mutate({ baseTemplateId: template.id, phases });
    }, 400);
    return () => clearTimeout(handle);
  }, [phases, template?.id, dirty]); // eslint-disable-line react-hooks/exhaustive-deps

  const violations = dirty ? (validate.data?.violations ?? []) : [];
  const valid = !dirty || (validate.data?.valid ?? true);

  function move(index: number, by: number) {
    const target = index + by;
    if (target < 0 || target >= phases.length) return;
    const next = [...phases];
    const a = next[index];
    const b = next[target];
    if (!a || !b) return;
    next[index] = b;
    next[target] = a;
    setPhases(next);
  }

  function removePhase(index: number) {
    setPhases(phases.filter((_, i) => i !== index));
  }

  function toggleDeliverable(index: number, artifact: string) {
    setPhases(
      phases.map((phase, i) => {
        if (i !== index) return phase;
        const has = phase.requiredDeliverables.includes(artifact);
        return {
          ...phase,
          requiredDeliverables: has
            ? phase.requiredDeliverables.filter((d) => d !== artifact)
            : [...phase.requiredDeliverables, artifact],
        };
      }),
    );
  }

  function onSave() {
    if (!template) return;
    save.mutate(
      { baseTemplateId: template.id, phases },
      { onSuccess: (version) => setSaved(version) },
    );
  }

  return (
    <div className="flow-workspace">
      <div className="page-heading">
        <div>
          <h1>Flow editor</h1>
          <span className="eyebrow">
            EDIT A TEMPLATE · SAVING CREATES A NEW VERSION FOR APPROVAL
          </span>
        </div>
        <Link className="action-secondary" to="/runs">
          Back to runs
        </Link>
      </div>

      {catalog.isError && (
        <p role="alert" className="field-help">
          Could not load the flow catalogue from the gateway.
        </p>
      )}

      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Template
        <select
          value={selectedId}
          onChange={(e) => setTemplateId(e.target.value)}
          aria-label="Template"
        >
          {flows.map((f) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
      </label>

      {/* Rule state, always visible. A validity indicator that only appears
          when something is wrong makes "nothing shown" ambiguous between
          "valid" and "not checked". */}
      <div className="glass rounded" data-testid="validation-panel">
        <p className="eyebrow">INVARIANTS</p>
        {!dirty && <p className="field-help">Unchanged from the template.</p>}
        {dirty && validate.isPending && <p className="field-help">Checking…</p>}
        {dirty && !validate.isPending && valid && (
          <p className="field-help">All rules pass. This flow can be saved.</p>
        )}
        {violations.length > 0 && (
          <ul aria-label="Invariant violations">
            {violations.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        )}
      </div>

      <ol className="flow-phases" aria-label="Phases">
        {phases.map((phase, i) => (
          <li key={phase.id} className="glass rounded">
            <div className="flex items-center justify-between">
              <div>
                <strong>{phase.title}</strong>
                <span className="mono-value">{phase.id}</span>
              </div>
              <div className="flex items-center gap-1">
                <button type="button" onClick={() => move(i, -1)} aria-label={`Move ${phase.id} earlier`}>
                  ↑
                </button>
                <button type="button" onClick={() => move(i, 1)} aria-label={`Move ${phase.id} later`}>
                  ↓
                </button>
                <button type="button" onClick={() => removePhase(i)} aria-label={`Remove ${phase.id}`}>
                  Remove
                </button>
              </div>
            </div>

            <p className="field-help">
              {phase.gate && !phase.gate.autoApprove ? phase.gate.name : 'No human gate'}
            </p>

            {phase.expectedArtifacts.length > 0 && (
              <fieldset>
                <legend className="eyebrow">REQUIRED AT THE GATE</legend>
                {phase.expectedArtifacts.map((artifact) => (
                  <label key={artifact} className="flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={phase.requiredDeliverables.includes(artifact)}
                      onChange={() => toggleDeliverable(i, artifact)}
                      aria-label={`Require ${artifact} from ${phase.id}`}
                    />
                    <span className="mono-value">{artifact}</span>
                  </label>
                ))}
              </fieldset>
            )}
          </li>
        ))}
      </ol>

      <div className="dialog-actions">
        <button type="button" onClick={onSave} disabled={!dirty || !valid || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save as new version'}
        </button>
        {!valid && (
          <span className="field-help">
            Fix the rules above before saving. An unstartable flow is not worth an approval.
          </span>
        )}
      </div>

      {save.isError && (
        <p role="alert" className="field-help">
          {String((save.error as { response?: { data?: { detail?: string } } })?.response?.data
            ?.detail ?? 'The edit could not be saved.')}
        </p>
      )}

      {saved && (
        <div className="glass rounded" data-testid="saved-version">
          <p className="eyebrow">SAVED — AWAITING APPROVAL</p>
          <p className="mono-value">{saved.versionId}</p>
          <ul aria-label="Changes">
            {saved.changes.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <p className="field-help">
            Nothing runs on this flow until it is approved. It is waiting in the approvals
            queue.
          </p>
        </div>
      )}
    </div>
  );
}
