import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { createTwinChatThread, sendTwinChatMessage } from '../api/endpoints/twin-chat';
import { createUpload } from '../api/endpoints/uploads';
import { conflictOf, getCreation, putBrief, refusalOf } from '../api/endpoints/create';

/**
 * The Create view's working state, per creation (project).
 *
 * It lives outside any component so it survives a switch to the Engineering
 * view and back: the unsent draft, attached references and queued
 * instructions are still there, and an instruction already sent keeps going
 * while the person is elsewhere. Drafts persist across a reload; attached
 * files cannot (a browser does not keep File objects), so they are uploaded
 * the moment they are attached and only their result is kept.
 */

export type Intent = 'ask' | 'change' | 'design';

export type AttachmentState = 'uploading' | 'added' | 'rejected' | 'failed';

export interface Attachment {
  id: string;
  name: string;
  size: number;
  state: AttachmentState;
  workProductId?: string;
  error?: string;
}

export type InstructionState = 'queued' | 'sending' | 'failed';

export interface Instruction {
  id: string;
  text: string;
  intent: Intent;
  references: string[];
  state: InstructionState;
  error?: string;
  createdAt: string;
}

export interface CreateSession {
  draft: string;
  intent: Intent;
  attachments: Attachment[];
  queue: Instruction[];
  threadId: string | null;
  selectedNodeId: string | null;
}

const EMPTY: CreateSession = {
  draft: '',
  intent: 'ask',
  attachments: [],
  queue: [],
  threadId: null,
  selectedNodeId: null,
};

/** The same instructions the Engineering chat drawer appends, so both strip them for display. */
export const INTENT_INSTRUCTION: Record<Intent, string> = {
  ask: '\nDiscuss using the supplied context. No tools are enabled for this turn. Do not claim to have changed or simulated the design.',
  change:
    '\nPropose a reviewable change using twin.propose_change; do not apply it. Include rationale and affected work products.',
  design: '\nUse only the explicitly selected tools for this request. Report actions and results accurately.',
};

export const CONTEXT_SUFFIX =
  /\n\nSelected twin object \(context snapshot\):|\n\nAttached twin work products|\nDiscuss using the supplied context\.|\nPropose a reviewable change using|\nUse only the explicitly selected tools/;

/** What kind of turn a stored user message was, from the instruction it carries. */
export function intentOf(content: string): Intent {
  if (content.includes('\nPropose a reviewable change using')) return 'change';
  if (content.includes('\nUse only the explicitly selected tools')) return 'design';
  return 'ask';
}

export function displayText(content: string): string {
  const cut = content.search(CONTEXT_SUFFIX);
  return (cut >= 0 ? content.slice(0, cut) : content).trim();
}

interface CreateStore {
  sessions: Record<string, CreateSession>;
  session: (projectId: string) => CreateSession;
  setDraft: (projectId: string, draft: string) => void;
  setIntent: (projectId: string, intent: Intent) => void;
  selectNode: (projectId: string, nodeId: string | null) => void;
  setThread: (projectId: string, threadId: string | null) => void;
  attach: (projectId: string, files: File[]) => void;
  removeAttachment: (projectId: string, id: string) => void;
  submit: (projectId: string, tools: Record<Intent, string[]>) => boolean;
  /** Send an answer given through a reply component (a choice, a measurement), leaving the draft alone. */
  reply: (projectId: string, text: string, tools: Record<Intent, string[]>) => boolean;
  retry: (projectId: string, id: string, tools: Record<Intent, string[]>) => void;
  restore: (projectId: string, id: string) => void;
  discard: (projectId: string, id: string) => void;
}

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq += 1)}`;

// One thread per creation, even when two instructions race to create it.
const threadCreation = new Map<string, Promise<string>>();
// Tools chosen when an instruction was submitted, kept out of persisted state.
const turnTools = new Map<string, string[]>();

export const useCreateStore = create<CreateStore>()(
  persist(
    (set, get) => {
      const patch = (projectId: string, fn: (s: CreateSession) => Partial<CreateSession>) =>
        set((st) => {
          const current = st.sessions[projectId] ?? EMPTY;
          return { sessions: { ...st.sessions, [projectId]: { ...current, ...fn(current) } } };
        });

      const updateInstruction = (projectId: string, id: string, change: Partial<Instruction>) =>
        patch(projectId, (s) => ({ queue: s.queue.map((i) => (i.id === id ? { ...i, ...change } : i)) }));

      async function ensureThread(projectId: string): Promise<string> {
        const known = get().session(projectId).threadId;
        if (known) return known;
        let pending = threadCreation.get(projectId);
        if (!pending) {
          pending = createTwinChatThread(projectId).then((t) => t.id);
          threadCreation.set(projectId, pending);
        }
        try {
          const id = await pending;
          patch(projectId, () => ({ threadId: id }));
          return id;
        } finally {
          threadCreation.delete(projectId);
        }
      }

      async function pump(projectId: string): Promise<void> {
        const s = get().session(projectId);
        if (s.queue.some((i) => i.state === 'sending')) return;
        const next = s.queue.find((i) => i.state === 'queued');
        if (!next) return;
        updateInstruction(projectId, next.id, { state: 'sending', error: undefined });
        try {
          const threadId = await ensureThread(projectId);
          const refs = next.references.length
            ? '\n\nAttached twin work products (references, not inline image content): ' +
              JSON.stringify(next.references.map((id) => ({ id })))
            : '';
          await sendTwinChatMessage(threadId, {
            content: next.text + refs + INTENT_INSTRUCTION[next.intent],
            tools: turnTools.get(next.id) ?? [],
          });
          turnTools.delete(next.id);
          patch(projectId, (cur) => ({ queue: cur.queue.filter((i) => i.id !== next.id) }));
        } catch (error) {
          updateInstruction(projectId, next.id, { state: 'failed', error: refusalOf(error) });
        }
        void pump(projectId);
      }

      async function recordReferences(projectId: string, ids: string[]): Promise<void> {
        // The brief lists the references; a concurrent edit is retried once
        // on top of the newer record rather than overwritten.
        for (let attempt = 0; attempt < 2; attempt += 1) {
          const view = await getCreation(projectId);
          const brief = view.creation.brief;
          const merged = [...brief.references, ...ids.filter((id) => !brief.references.includes(id))];
          try {
            await putBrief(
              projectId,
              { ...brief, references: merged },
              { expected_version: view.creation.version, summary: 'References added' },
            );
            return;
          } catch (error) {
            if (!conflictOf(error)) throw error;
          }
        }
      }

      return {
        sessions: {},
        session: (projectId) => get().sessions[projectId] ?? EMPTY,
        setDraft: (projectId, draft) => patch(projectId, () => ({ draft })),
        setIntent: (projectId, intent) => patch(projectId, () => ({ intent })),
        selectNode: (projectId, nodeId) => patch(projectId, () => ({ selectedNodeId: nodeId })),
        setThread: (projectId, threadId) => patch(projectId, () => ({ threadId })),

        attach: (projectId, files) => {
          const added = files.map((file) => ({ file, id: nextId('ref') }));
          patch(projectId, (s) => ({
            attachments: [
              ...s.attachments,
              ...added.map(({ file, id }) => ({ id, name: file.name, size: file.size, state: 'uploading' as const })),
            ],
          }));
          void (async () => {
            try {
              const upload = await createUpload(
                added.map((a) => a.file),
                { projectId, approve: true, description: 'Reference added in Create' },
              );
              const ids: string[] = [];
              patch(projectId, (s) => ({
                attachments: s.attachments.map((a) => {
                  const idx = added.findIndex((x) => x.id === a.id);
                  if (idx < 0) return a;
                  const staged = upload.files.find((f) => f.name === added[idx]!.file.name);
                  if (!staged) return { ...a, state: 'failed', error: 'The gateway did not list this file' };
                  if (staged.error) return { ...a, state: 'rejected', error: staged.error };
                  if (staged.work_product_id) ids.push(staged.work_product_id);
                  return staged.work_product_id
                    ? { ...a, state: 'added', workProductId: staged.work_product_id }
                    : { ...a, state: 'failed', error: 'Not imported' };
                }),
              }));
              if (ids.length) await recordReferences(projectId, ids);
            } catch (error) {
              const message = refusalOf(error);
              patch(projectId, (s) => ({
                attachments: s.attachments.map((a) =>
                  added.some((x) => x.id === a.id) && a.state === 'uploading'
                    ? { ...a, state: 'failed', error: message }
                    : a,
                ),
              }));
            }
          })();
        },

        removeAttachment: (projectId, id) =>
          patch(projectId, (s) => ({ attachments: s.attachments.filter((a) => a.id !== id) })),

        submit: (projectId, tools) => {
          const s = get().session(projectId);
          const text = s.draft.trim();
          if (!text) return false;
          if (s.attachments.some((a) => a.state === 'uploading')) return false;
          const id = nextId('ins');
          turnTools.set(id, tools[s.intent] ?? []);
          patch(projectId, (cur) => ({
            draft: '',
            attachments: cur.attachments.filter((a) => a.state !== 'added'),
            queue: [
              ...cur.queue,
              {
                id,
                text,
                intent: cur.intent,
                references: cur.attachments.filter((a) => a.workProductId).map((a) => a.workProductId!),
                state: 'queued',
                createdAt: new Date().toISOString(),
              },
            ],
          }));
          void pump(projectId);
          return true;
        },

        reply: (projectId, text, tools) => {
          const answer = text.trim();
          if (!answer) return false;
          const id = nextId('ins');
          turnTools.set(id, tools.ask ?? []);
          patch(projectId, (cur) => ({
            queue: [
              ...cur.queue,
              { id, text: answer, intent: 'ask', references: [], state: 'queued', createdAt: new Date().toISOString() },
            ],
          }));
          void pump(projectId);
          return true;
        },

        retry: (projectId, id, tools) => {
          const ins = get().session(projectId).queue.find((i) => i.id === id);
          if (!ins) return;
          turnTools.set(id, tools[ins.intent] ?? []);
          updateInstruction(projectId, id, { state: 'queued', error: undefined });
          void pump(projectId);
        },

        // Put a failed instruction back in the composer to edit.
        restore: (projectId, id) => {
          const ins = get().session(projectId).queue.find((i) => i.id === id);
          if (!ins) return;
          patch(projectId, (s) => ({
            draft: s.draft ? `${s.draft}\n${ins.text}` : ins.text,
            intent: ins.intent,
            queue: s.queue.filter((i) => i.id !== id),
          }));
        },

        discard: (projectId, id) =>
          patch(projectId, (s) => ({ queue: s.queue.filter((i) => i.id !== id || i.state === 'sending') })),
      };
    },
    {
      name: 'metaforge.create',
      storage: createJSONStorage(() => localStorage),
      // Files and in-flight sends do not survive a reload; the text does.
      partialize: (st) => ({
        sessions: Object.fromEntries(
          Object.entries(st.sessions).map(([id, s]) => [
            id,
            {
              ...EMPTY,
              draft: s.draft,
              intent: s.intent,
              threadId: s.threadId,
              selectedNodeId: s.selectedNodeId,
              // An unsent instruction comes back as a failed one to retry, never resent on its own.
              queue: s.queue.map((i) => ({
                ...i,
                state: 'failed' as const,
                error:
                  i.state === 'sending'
                    ? 'The page closed while this was being sent. Check the conversation before retrying.'
                    : (i.error ?? 'Not sent before the page closed'),
              })),
            },
          ]),
        ),
      }),
    },
  ),
);
