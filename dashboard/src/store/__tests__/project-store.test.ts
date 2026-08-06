import { describe, it, expect, beforeEach } from 'vitest';
import { useProjectStore } from '../project-store';

const reset = () =>
  useProjectStore.setState({ activeProjectId: null, hasSelected: false });

describe('useProjectStore', () => {
  beforeEach(reset);

  it('defaults to "All projects" (null) and unselected', () => {
    const s = useProjectStore.getState();
    expect(s.activeProjectId).toBeNull();
    expect(s.hasSelected).toBe(false);
  });

  it('setActiveProject sets the id and marks selected', () => {
    useProjectStore.getState().setActiveProject('proj-1');
    const s = useProjectStore.getState();
    expect(s.activeProjectId).toBe('proj-1');
    expect(s.hasSelected).toBe(true);
  });

  it('setActiveProject(null) is a deliberate "All projects" pick, not a reset', () => {
    useProjectStore.getState().setActiveProject('proj-1');
    useProjectStore.getState().setActiveProject(null);
    const s = useProjectStore.getState();
    expect(s.activeProjectId).toBeNull();
    expect(s.hasSelected).toBe(true);
  });

  it('autoSelectIfUnset picks a project only when nothing has been selected yet', () => {
    useProjectStore.getState().autoSelectIfUnset('newest-project');
    expect(useProjectStore.getState().activeProjectId).toBe('newest-project');
    expect(useProjectStore.getState().hasSelected).toBe(true);
  });

  it('autoSelectIfUnset is a no-op once a selection exists', () => {
    useProjectStore.getState().setActiveProject('user-pick');
    useProjectStore.getState().autoSelectIfUnset('newest-project');
    expect(useProjectStore.getState().activeProjectId).toBe('user-pick');
  });

  it('autoSelectIfUnset is a no-op once the user has explicitly chosen "All projects"', () => {
    useProjectStore.getState().setActiveProject(null);
    useProjectStore.getState().autoSelectIfUnset('newest-project');
    expect(useProjectStore.getState().activeProjectId).toBeNull();
  });
});
