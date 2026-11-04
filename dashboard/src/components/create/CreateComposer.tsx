import { useRef } from 'react';
import { AlertTriangle, ArrowUp, Check, Loader2, Paperclip, X } from 'lucide-react';
import { useCreateStore, type Intent } from '../../store/create-store';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { ReferenceChip } from './ReferenceChip';
import { referenceRecord } from '../../api/endpoints/create';
import { useCreation } from '../../hooks/use-creation';

const INTENTS: Array<{ id: Intent; label: string; hint: string }> = [
  { id: 'ask', label: 'Ask', hint: 'Discuss the product. Nothing is changed.' },
  { id: 'change', label: 'Change', hint: 'Propose a change for you to approve before it is applied.' },
  { id: 'design', label: 'Design', hint: 'Let the agent run a design pass with its tools. Writes still wait for you.' },
];

const ACCEPT = 'image/*,.pdf,.txt,.md,.csv,.json,.step,.stp,.stl,.3mf,.obj,.glb,.gltf,.dxf,.svg,.kicad_pcb,.kicad_sch,.zip';

interface CreateComposerProps {
  projectId: string;
  tools: Record<Intent, string[]>;
  /** A pass is running: new instructions queue behind it. */
  busy: boolean;
  placeholder?: string;
  autoFocus?: boolean;
}

export function CreateComposer({ projectId, tools, busy, placeholder, autoFocus }: CreateComposerProps) {
  const session = useCreateStore((s) => s.session(projectId));
  const setDraft = useCreateStore((s) => s.setDraft);
  const setIntent = useCreateStore((s) => s.setIntent);
  const attach = useCreateStore((s) => s.attach);
  const removeAttachment = useCreateStore((s) => s.removeAttachment);
  const updateAttachment = useCreateStore((s) => s.updateAttachment);
  // FORGE-794: an added reference shows its role and whether it is included.
  const brief = useCreation(session.attachments.some((a) => a.workProductId) ? projectId : null).data?.creation
    .brief;
  const submit = useCreateStore((s) => s.submit);
  const fileInput = useRef<HTMLInputElement>(null);

  const uploading = session.attachments.some((a) => a.state === 'uploading');
  const changeUnavailable = session.intent === 'change' && tools.change.length === 0;
  const canSend = !!session.draft.trim() && !uploading && !changeUnavailable;

  const send = () => {
    if (canSend) submit(projectId, tools);
  };

  return (
    <form
      className="cv-composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
      onPaste={(e) => {
        const files = Array.from(e.clipboardData.files);
        if (files.length) {
          e.preventDefault();
          attach(projectId, files);
        }
      }}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const files = Array.from(e.dataTransfer.files);
        if (files.length) attach(projectId, files);
      }}
    >
      <div className="cv-intents" role="radiogroup" aria-label="What should happen with this message">
        {INTENTS.map((it) => (
          <button
            key={it.id}
            type="button"
            role="radio"
            aria-checked={session.intent === it.id}
            className={`cv-intent ${session.intent === it.id ? 'is-on' : ''}`}
            title={it.hint}
            onClick={() => setIntent(projectId, it.id)}
          >
            {it.label}
          </button>
        ))}
        <span className="cv-intent-hint">{INTENTS.find((i) => i.id === session.intent)?.hint}</span>
      </div>

      {session.attachments.length > 0 && (
        <ul className="cv-attachments" aria-label="References">
          {session.attachments.map((a) => (
            <li key={a.id} className={`cv-attachment is-${a.state}`} title={a.error ?? a.name}>
              {a.state === 'uploading' ? (
                <Loader2 size={13} className="cv-spin" aria-hidden="true" />
              ) : a.state === 'added' ? (
                <Check size={13} aria-hidden="true" />
              ) : (
                <AlertTriangle size={13} aria-hidden="true" />
              )}
              {a.state === 'added' && a.workProductId ? (
                <ReferenceChip
                  projectId={projectId}
                  record={{
                    ...referenceRecord(brief, a.workProductId, a.name),
                    ...(a.role ? { role: a.role } : {}),
                    ...(a.active === undefined ? {} : { active: a.active }),
                  }}
                  onLocalChange={(change) =>
                    updateAttachment(projectId, a.id, {
                      ...(change.role ? { role: change.role } : {}),
                      ...(change.active === undefined ? {} : { active: change.active }),
                    })
                  }
                />
              ) : (
                <span className="cv-attachment-name">{a.name}</span>
              )}
              <span className="cv-attachment-state">
                {a.state === 'uploading'
                  ? 'Uploading'
                  : a.state === 'added'
                    ? 'Added'
                    : a.state === 'rejected'
                      ? `Not supported: ${a.error}`
                      : `Failed: ${a.error}`}
              </span>
              <button
                type="button"
                className="cv-icon-btn"
                aria-label={`Remove ${a.name}`}
                onClick={() => removeAttachment(projectId, a.id)}
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="cv-composer-row">
        <button
          type="button"
          className="cv-icon-btn cv-attach"
          aria-label="Attach photos, sketches, documents or design files"
          title="Attach photos, sketches, documents or design files"
          onClick={() => fileInput.current?.click()}
        >
          <Paperclip size={17} />
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept={ACCEPT}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) attach(projectId, files);
            e.target.value = '';
          }}
        />
        <AutoGrowTextarea
          className="cv-textarea"
          aria-label="Describe your product or ask for a change"
          placeholder={placeholder ?? 'Describe what you want to make, or what to change…'}
          value={session.draft}
          autoFocus={autoFocus}
          onChange={(e) => setDraft(projectId, e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button type="submit" className="cv-send" disabled={!canSend} aria-label={busy ? 'Queue this instruction' : 'Send'}>
          <ArrowUp size={18} />
        </button>
      </div>
      <p className="cv-composer-foot" aria-live="polite">
        {changeUnavailable
          ? 'Proposing changes needs the twin.propose_change tool, which this gateway does not offer.'
          : uploading
            ? 'Waiting for references to finish uploading.'
            : busy
              ? 'A pass is running. Anything you send now is queued and sent next.'
              : 'Enter to send · Shift+Enter for a new line · paste or drop files to attach'}
      </p>
    </form>
  );
}
