// The main things the MetaForge plugin's commands do (/metaforge:status,
// design, fea, gate, approvals, verify), as actions in both chats. Choosing
// one fills the message in plain words and turns tools on; nothing runs until
// the person sends it. The agent picks the matching playbook itself.

export interface QuickAction {
  id: string;
  label: string;
  hint: string;
  /** The message, about `target` (a part, or the product as a whole). */
  prompt: (target: string) => string;
}

export const QUICK_ACTIONS: QuickAction[] = [
  {
    id: 'status',
    label: 'Where it stands',
    hint: 'What is built, and what is not proven yet',
    prompt: (t) => `Where does ${t} stand? What is built, and which requirements have no evidence yet?`,
  },
  {
    id: 'design',
    label: 'Design this',
    hint: 'Design or revise it, then save the model',
    prompt: (t) => `Design or revise ${t} so it meets its requirements, and save the model to the design record.`,
  },
  {
    id: 'fea',
    label: 'Run a stress check',
    hint: 'Analyse it under load and keep the result as evidence',
    prompt: (t) =>
      `Run a stress check on ${t} under its expected loads, and record the result as evidence for the revision you checked.`,
  },
  {
    id: 'gate',
    label: 'Review the gate',
    hint: 'Is it ready for the next stage, and what is missing',
    prompt: (t) => `Is ${t} ready to move to the next stage? List what is missing.`,
  },
  {
    id: 'approvals',
    label: "What's waiting for me",
    hint: 'Decisions, held changes and gates waiting for you',
    prompt: () => 'What is waiting for my decision on this project?',
  },
  {
    id: 'verify',
    label: 'Did it work?',
    hint: 'Check the latest run did what it was for',
    prompt: (t) =>
      `Did the latest design run for ${t} achieve what it was meant to? Say what is verified and what is not.`,
  },
];
