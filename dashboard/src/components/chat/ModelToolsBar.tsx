import { useEffect, useRef, useState } from 'react';
import { useChatStore } from '@/store/chat-store';
import { useHarnessProviders, useHarnessModels, useHarnessTools } from '@/hooks/use-harness';

// ---------------------------------------------------------------------------
// KC tokens
// ---------------------------------------------------------------------------

const KC = {
  surfaceHigh: '#282a30',
  surfaceLow: '#191b22',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primary: '#e67e22',
  border: 'rgba(65,72,90,0.3)',
};

const pillStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 26,
  padding: '0 10px',
  background: KC.surfaceHigh,
  border: `1px solid ${KC.border}`,
  borderRadius: 9999,
  color: KC.onSurface,
  fontSize: 12,
  fontFamily: 'Inter, sans-serif',
  cursor: 'pointer',
  userSelect: 'none',
  whiteSpace: 'nowrap',
};

/**
 * Compact model + tools/connectors selector, rendered just above the chat
 * composer (Claude / opencode-style). Selection lives in the chat store and is
 * merged into every outgoing message by useSendChatMessage (MET-548).
 */
export function ModelToolsBar() {
  const {
    selectedProvider,
    selectedModel,
    enabledTools,
    setModel,
    setEnabledTools,
  } = useChatStore();

  const { data: providersResult } = useHarnessProviders();
  const providers = providersResult?.providers ?? [];
  const activeProvider = selectedProvider ?? providersResult?.activeProvider ?? null;
  const activeModel = selectedModel ?? providersResult?.activeModel ?? null;

  const { data: models = [] } = useHarnessModels(activeProvider);
  const { data: tools = [] } = useHarnessTools();

  // Initialize the store selection from the server's active provider/model once.
  const initedRef = useRef(false);
  useEffect(() => {
    if (
      !initedRef.current &&
      selectedProvider === null &&
      providersResult?.activeProvider
    ) {
      initedRef.current = true;
      setModel(providersResult.activeProvider, providersResult.activeModel ?? null);
    }
  }, [providersResult, selectedProvider, setModel]);

  const [menu, setMenu] = useState<'none' | 'model' | 'tools'>('none');
  const barRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (barRef.current && !barRef.current.contains(e.target as Node)) setMenu('none');
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const toolCount = enabledTools === null ? tools.length : enabledTools.length;
  const modelLabel = activeModel || 'Select model';

  function toggleTool(id: string) {
    const current = enabledTools ?? tools.map((t) => t.id); // null = all
    const next = current.includes(id)
      ? current.filter((t) => t !== id)
      : [...current, id];
    setEnabledTools(next);
  }
  const isToolOn = (id: string) => (enabledTools === null ? true : enabledTools.includes(id));

  return (
    <div
      ref={barRef}
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 12px',
        borderTop: `1px solid ${KC.border}`,
      }}
    >
      {/* Model pill */}
      <button
        type="button"
        style={pillStyle}
        onClick={() => setMenu(menu === 'model' ? 'none' : 'model')}
        aria-label="Select model"
      >
        <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.primary }}>
          neurology
        </span>
        <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {activeProvider ? `${activeProvider} · ${modelLabel}` : modelLabel}
        </span>
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
          expand_more
        </span>
      </button>

      {/* Tools pill */}
      <button
        type="button"
        style={pillStyle}
        onClick={() => setMenu(menu === 'tools' ? 'none' : 'tools')}
        aria-label="Select tools and connectors"
      >
        <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.primary }}>
          build
        </span>
        <span>{tools.length === 0 ? 'No tools' : `Tools · ${toolCount}/${tools.length}`}</span>
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
          expand_more
        </span>
      </button>

      {/* Model menu */}
      {menu === 'model' && (
        <div style={menuStyle}>
          <div style={menuLabel}>Provider</div>
          <select
            value={activeProvider ?? ''}
            onChange={(e) => setModel(e.target.value || null, null)}
            style={selectStyle}
          >
            {!activeProvider && <option value="">Select…</option>}
            {providers.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.configured}>
                {p.id}
                {p.configured ? '' : ' (not configured)'}
              </option>
            ))}
          </select>

          <div style={{ ...menuLabel, marginTop: 8 }}>Model</div>
          {models.length > 0 ? (
            <select
              value={activeModel ?? ''}
              onChange={(e) => setModel(activeProvider, e.target.value || null)}
              style={selectStyle}
            >
              {!activeModel && <option value="">Select…</option>}
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={activeModel ?? ''}
              placeholder="model id (free text)"
              onChange={(e) => setModel(activeProvider, e.target.value || null)}
              style={selectStyle}
            />
          )}
        </div>
      )}

      {/* Tools menu */}
      {menu === 'tools' && (
        <div style={{ ...menuStyle, left: 'auto' }}>
          <div style={menuLabel}>Tools / connectors</div>
          {tools.length === 0 ? (
            <div style={{ fontSize: 12, color: KC.onSurfaceVariant, padding: '6px 2px' }}>
              No tools available. Connect an MCP bridge to the gateway to drive tools from chat.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto' }}>
              {tools.map((t) => (
                <label
                  key={t.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    fontSize: 12,
                    color: KC.onSurface,
                    cursor: 'pointer',
                  }}
                >
                  <input type="checkbox" checked={isToolOn(t.id)} onChange={() => toggleTool(t.id)} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.name}
                    <span style={{ color: KC.onSurfaceVariant }}> · {t.server}</span>
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const menuStyle: React.CSSProperties = {
  position: 'absolute',
  bottom: 'calc(100% + 4px)',
  left: 12,
  width: 300,
  background: KC.surfaceLow,
  border: `1px solid ${KC.border}`,
  borderRadius: 8,
  padding: 10,
  zIndex: 60,
  boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
};

const menuLabel: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: KC.onSurfaceVariant,
  marginBottom: 4,
};

const selectStyle: React.CSSProperties = {
  width: '100%',
  background: KC.surfaceHigh,
  border: `1px solid ${KC.border}`,
  borderRadius: 4,
  color: KC.onSurface,
  padding: '6px 8px',
  fontSize: 12,
  fontFamily: 'Inter, sans-serif',
  outline: 'none',
};
