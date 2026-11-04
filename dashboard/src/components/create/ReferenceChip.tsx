import { useState } from 'react';
import { ChevronDown, Eye, EyeOff } from 'lucide-react';
import {
  REFERENCE_ROLE_LABELS,
  REFERENCE_ROLES,
  extractReferenceFacts,
  putReference,
  refusalOf,
  type ReferenceRecord,
  type ReferenceRole,
  type ReferenceUpdate,
} from '../../api/endpoints/create';
import { useCreationWrite } from '../../hooks/use-creation';

interface ReferenceChipProps {
  projectId: string;
  record: ReferenceRecord;
  /** Called with the change before it is saved, so a local copy can follow at once. */
  onLocalChange?: (change: ReferenceUpdate) => void;
}

/**
 * One reference as a compact chip: its role and whether the next instruction
 * includes it (FORGE-794, UXS-004). Protected features and facts open only
 * when asked for.
 */
export function ReferenceChip({ projectId, record, onLocalChange }: ReferenceChipProps) {
  const [open, setOpen] = useState(false);
  const [protectedText, setProtectedText] = useState(record.protected_features.join(', '));
  const write = useCreationWrite(projectId, (update: ReferenceUpdate) =>
    putReference(projectId, record.work_product_id, update),
  );
  const facts = useCreationWrite(projectId, () => extractReferenceFacts(projectId, record.work_product_id));
  const name = record.name || 'Reference';

  const change = (update: ReferenceUpdate) => {
    onLocalChange?.(update);
    write.mutate(update);
  };

  const stated = record.facts.filter((f) => f.basis === 'stated').length;
  const error = write.error ?? facts.error;

  return (
    <span className={`cv-ref-chip ${record.active ? '' : 'is-off'}`} data-testid="reference-chip">
      <span className="cv-ref-name" title={name}>
        {name}
      </span>
      <select
        className="cv-ref-role"
        aria-label={`Role of ${name}`}
        value={record.role}
        onChange={(e) => change({ role: e.target.value as ReferenceRole })}
      >
        {REFERENCE_ROLES.map((role) => (
          <option key={role} value={role}>
            {REFERENCE_ROLE_LABELS[role]}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="cv-icon-btn"
        aria-pressed={record.active}
        aria-label={record.active ? `Leave ${name} out of the next instruction` : `Include ${name} in the next instruction`}
        title={record.active ? 'Included in the next instruction' : 'Left out of the next instruction'}
        onClick={() => change({ active: !record.active })}
      >
        {record.active ? <Eye size={12} aria-hidden="true" /> : <EyeOff size={12} aria-hidden="true" />}
        <span className="cv-ref-state">{record.active ? 'Included' : 'Left out'}</span>
      </button>
      <button
        type="button"
        className="cv-icon-btn"
        aria-expanded={open}
        aria-label={`More about ${name}`}
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronDown size={12} aria-hidden="true" />
      </button>
      {open && (
        <span className="cv-ref-more" role="group" aria-label={`Details of ${name}`}>
          <label>
            <span>Keep unchanged</span>
            <input
              type="text"
              value={protectedText}
              placeholder="e.g. mounting holes, proportions"
              onChange={(e) => setProtectedText(e.target.value)}
              onBlur={() =>
                change({
                  protected_features: protectedText
                    .split(',')
                    .map((p) => p.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <button type="button" className="cv-chip-btn" disabled={facts.isPending} onClick={() => facts.mutate(undefined)}>
            {facts.isPending ? 'Reading…' : 'Find facts'}
          </button>
          {record.facts.length > 0 && (
            <span className="cv-ref-facts">
              {record.facts.length} fact{record.facts.length === 1 ? '' : 's'} · {stated} stated,{' '}
              {record.facts.length - stated} estimated
            </span>
          )}
          {error && <span className="cv-ref-error">{refusalOf(error)}</span>}
        </span>
      )}
    </span>
  );
}
