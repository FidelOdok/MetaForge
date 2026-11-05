import { useEffect, useId } from 'react';
import { useHarnessModels, useHarnessProviders } from '../../hooks/use-harness';
import { useModelChoice } from '../../store/model-choice';

/** What the chat will use: the person's choice, or the gateway default. */
export function useModelLabel(): string {
  const providers = useHarnessProviders();
  const { provider, model } = useModelChoice();
  if (model.trim()) return model.trim();
  if (provider) return `${provider} default`;
  return providers.data?.activeModel || 'Default model';
}

/**
 * The person's choice of model, shared by both chats and kept in this browser.
 * Leaving it on the default sends nothing, so the gateway's own choice applies.
 */
export function ModelPicker({ disabled = false }: { disabled?: boolean }) {
  const providers = useHarnessProviders();
  const { provider, model, choose, reset } = useModelChoice();
  const models = useHarnessModels(provider || providers.data?.activeProvider || null);
  const listId = useId();
  const custom = !!provider || !!model.trim();

  // A saved provider that is no longer connected falls back to the default, rather than failing every turn.
  useEffect(() => {
    if (!provider || !providers.data) return;
    if (!providers.data.providers.some((p) => p.id === provider && p.configured)) reset();
  }, [provider, providers.data, reset]);

  return (
    <div className="mb-model-picker">
      <label>
        Provider
        <select
          value={provider}
          disabled={disabled}
          onChange={(e) => choose({ provider: e.target.value, model: '' })}
        >
          <option value="">Default · {providers.data?.activeProvider || 'not set up'}</option>
          {providers.data?.providers.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.configured}>
              {p.id}
              {p.configured ? '' : ' · not connected'}
            </option>
          ))}
        </select>
      </label>
      <label>
        Model
        <input
          list={listId}
          value={model}
          disabled={disabled}
          onChange={(e) => choose({ model: e.target.value })}
          placeholder={(!provider && providers.data?.activeModel) || 'Provider default'}
        />
        <datalist id={listId}>
          {models.data?.map((m) => <option key={m} value={m} />)}
        </datalist>
      </label>
      {models.isError && <p>The model list is unavailable. Enter a model ID.</p>}
      <p>
        {custom
          ? 'Used for your messages in both chats, in this browser.'
          : 'Using the default set for this workspace. A choice here applies to your messages only.'}
      </p>
      {custom && (
        <button type="button" className="mb-model-reset" disabled={disabled} onClick={reset}>
          Use the default
        </button>
      )}
    </div>
  );
}
