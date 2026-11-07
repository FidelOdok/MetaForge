import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PANELS_KEY, readCollapsed, useCreateLayoutStore } from '../create-layout-store';

const P = 'proj-layout';

beforeEach(() => {
  window.localStorage.clear();
  useCreateLayoutStore.getState().reset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('create layout store', () => {
  it('keeps the collapsed conversation as the person preference across loads', () => {
    useCreateLayoutStore.getState().setCollapsed('chat', true);
    expect(JSON.parse(window.localStorage.getItem(PANELS_KEY) ?? '{}')).toEqual({ chat: true });
    useCreateLayoutStore.getState().reset();
    expect(useCreateLayoutStore.getState().collapsed).toEqual({ chat: true });
  });

  it('starts expanded, and still collapses for the page, when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readCollapsed()).toEqual({ chat: false });
    useCreateLayoutStore.getState().setCollapsed('chat', true);
    expect(useCreateLayoutStore.getState().collapsed.chat).toBe(true);
  });

  it('ignores a stored value that is not the expected shape', () => {
    window.localStorage.setItem(PANELS_KEY, '"nonsense"');
    expect(readCollapsed()).toEqual({ chat: false });
  });

  it('holds one drawer panel at a time, which collapses and closes', () => {
    const s = useCreateLayoutStore.getState();
    s.openDrawer(P, 'editable');
    s.openDrawer(P, 'plane');
    expect(useCreateLayoutStore.getState().stage(P).drawer).toBe('plane');
    s.setDrawerCollapsed(P, true);
    expect(useCreateLayoutStore.getState().stage(P).drawerCollapsed).toBe(true);
    s.closeDrawer(P);
    expect(useCreateLayoutStore.getState().stage(P)).toEqual({ drawer: null, drawerCollapsed: false });
  });
});
