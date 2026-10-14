import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/** The mode is read from localStorage once, as the store module initialises,
 * so each case needs a fresh module registry rather than a fresh store. */
async function loadStore(stored: string | null | (() => never)) {
  vi.resetModules();
  const getItem = typeof stored === 'function' ? stored : () => stored;
  vi.stubGlobal('localStorage', {
    getItem: vi.fn(getItem),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  });
  const { useViewerStore } = await import('../viewer-store');
  return useViewerStore;
}

describe('viewer-store camera mode', () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it('defaults to free orbit, which has no pole to stop a drag at', async () => {
    const useViewerStore = await loadStore(null);

    expect(useViewerStore.getState().cameraMode).toBe('free');
  });

  it('honours a remembered turntable', async () => {
    const useViewerStore = await loadStore('turntable');

    expect(useViewerStore.getState().cameraMode).toBe('turntable');
  });

  it('falls back to the default for a value it does not recognise', async () => {
    const useViewerStore = await loadStore('orbitcam');

    expect(useViewerStore.getState().cameraMode).toBe('free');
  });

  it('still loads when storage throws, as it does in a private window', async () => {
    const useViewerStore = await loadStore(() => {
      throw new Error('SecurityError: site data blocked');
    });

    expect(useViewerStore.getState().cameraMode).toBe('free');
  });

  it('remembers a choice, and a storage failure never breaks the switch', async () => {
    const useViewerStore = await loadStore(null);
    const failing = vi.fn(() => {
      throw new Error('QuotaExceededError');
    });
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: failing });

    useViewerStore.getState().setCameraMode('turntable');

    // The preference is simply not remembered; the mode still changes.
    expect(failing).toHaveBeenCalled();
    expect(useViewerStore.getState().cameraMode).toBe('turntable');
  });
});
