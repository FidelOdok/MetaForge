import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor } from '../../../../test/test-utils';
import { useCreateLayoutStore } from '../../../../store/create-layout-store';

vi.mock('../../../../hooks/use-creation', () => ({ useCreation: () => ({ data: undefined }) }));
vi.mock('../../../../hooks/use-sketch-plane', () => ({ useSketchPlane: () => ({ data: undefined }) }));
vi.mock('../../../sketch/SketchLayer', () => ({
  SketchLayer: (props: { onRequestPanel?: (p: 'plane') => void; editableHost?: HTMLElement | null }) => (
    <div data-testid="sketch-layer" data-editable-host={String(!!props.editableHost)}>
      <button type="button" onClick={() => props.onRequestPanel?.('plane')}>
        Use in 3D
      </button>
    </div>
  ),
}));
vi.mock('../../../sketch/GeneratePanel', async (orig) => ({
  ...(await orig<typeof import('../../../sketch/GeneratePanel')>()),
  GeneratePanel: ({ docked }: { docked?: boolean }) => <div data-testid="generate-panel" data-docked={String(!!docked)} />,
}));
vi.mock('../../../sketch/PlanePrompt', () => ({ PlanePrompt: () => <div data-testid="plane-prompt" /> }));

import { SketchWorkspace } from '../SketchWorkspace';

const P = 'proj-sketch';

beforeEach(() => {
  window.localStorage.clear();
  useCreateLayoutStore.getState().reset();
});

describe('Sketch, in Imagine', () => {
  it('shows the paper on its own, with no 3D view under it', () => {
    render(<SketchWorkspace projectId={P} nodes={[]} />);
    expect(screen.getByTestId('sketch-paper')).toContainElement(screen.getByTestId('sketch-layer'));
    expect(screen.queryByTestId('preview-host')).toBeNull();
    expect(screen.queryByTestId('stage-trace')).toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Trace/ })).toBeNull();
  });

  it('opens one drawer panel at a time beside the drawing, which collapses and closes', async () => {
    render(<SketchWorkspace projectId={P} nodes={[]} />);
    await userEvent.click(screen.getByRole('button', { name: /Generate a concept/ }));
    const drawer = screen.getByTestId('stage-drawer');
    expect(drawer).toHaveAttribute('data-panel', 'generate');
    expect(screen.getByTestId('generate-panel')).toHaveAttribute('data-docked', 'true');
    expect(screen.getByTestId('sketch-paper').contains(drawer)).toBe(false);

    await userEvent.click(screen.getByRole('button', { name: /References/ }));
    expect(screen.getAllByTestId('stage-drawer')).toHaveLength(1);
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'references');

    await userEvent.click(screen.getByRole('button', { name: 'Collapse References' }));
    expect(screen.getByTestId('stage-drawer')).toHaveClass('is-collapsed');
    await userEvent.click(screen.getByRole('button', { name: 'Expand References' }));
    expect(screen.getByText('No references yet.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close References' }));
    expect(screen.queryByTestId('stage-drawer')).toBeNull();
  });

  it('puts the editable sketch in the drawer, and the plane choice replaces it', async () => {
    render(<SketchWorkspace projectId={P} nodes={[]} />);
    act(() => useCreateLayoutStore.getState().openDrawer(P, 'editable'));
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'editable');
    await waitFor(() => expect(screen.getByTestId('sketch-layer')).toHaveAttribute('data-editable-host', 'true'));
    await userEvent.click(screen.getByRole('button', { name: 'Use in 3D' }));
    expect(screen.getByTestId('stage-drawer')).toHaveAttribute('data-panel', 'plane');
    expect(screen.getByTestId('sketch-layer')).toHaveAttribute('data-editable-host', 'false');
  });
});
