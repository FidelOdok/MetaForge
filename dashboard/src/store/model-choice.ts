import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

// ---------------------------------------------------------------------------
// The person's own choice of model for the chat agent, shared by the Create
// conversation and the Engineering chat. Empty means "the gateway default",
// which an operator sets on the Settings page; this choice never changes it.
// Kept in this browser only, so it is a preference, not a gateway setting.
// ---------------------------------------------------------------------------

export interface ModelChoice {
  /** Provider id, or '' for the gateway's active provider. */
  provider: string;
  /** Model id, or '' for that provider's default. */
  model: string;
}

interface ModelChoiceState extends ModelChoice {
  choose: (choice: Partial<ModelChoice>) => void;
  /** Back to the gateway default. */
  reset: () => void;
}

export const useModelChoice = create<ModelChoiceState>()(
  persist(
    (set) => ({
      provider: '',
      model: '',
      choose: (choice) => set(choice),
      reset: () => set({ provider: '', model: '' }),
    }),
    {
      name: 'metaforge.model-choice',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ provider: s.provider, model: s.model }),
    },
  ),
);

/** The fields to add to a chat message: nothing when the default is in use. */
export function modelChoiceFields(choice: ModelChoice): { provider?: string; model?: string } {
  const model = choice.model.trim();
  return { ...(choice.provider ? { provider: choice.provider } : {}), ...(model ? { model } : {}) };
}
