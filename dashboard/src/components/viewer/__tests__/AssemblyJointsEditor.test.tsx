import { describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';

const mockUpdateAssemblyJoints = vi.fn();
vi.mock('../../../api/endpoints/twin', () => ({
  updateAssemblyJoints: (nodeId: string, joints: unknown) =>
    mockUpdateAssemblyJoints(nodeId, joints),
}));

import { AssemblyJointsEditor } from '../AssemblyJointsEditor';

const PARTS = [
  { node_id: 'p1', link_name: 'base_link' },
  { node_id: 'p2', link_name: 'upper_arm' },
];

describe('AssemblyJointsEditor', () => {
  it('renders existing joints', () => {
    render(
      <AssemblyJointsEditor
        nodeId="n1"
        parts={PARTS}
        joints={[{ name: 'shoulder', type: 'revolute', base: 'base_link', follower: 'upper_arm', axis: [0, 0, 1], anchor: [0, 0, 0] }]}
      />,
    );
    expect(screen.getByText(/shoulder/)).toBeInTheDocument();
    expect(screen.getByText(/base_link → upper_arm/)).toBeInTheDocument();
  });

  it('shows no joints message area empty when there are none, and add is disabled with fewer than 2 parts', () => {
    render(<AssemblyJointsEditor nodeId="n1" parts={[PARTS[0]!]} joints={[]} />);
    expect(screen.getByRole('button', { name: '+ Add mate/joint' })).toBeDisabled();
  });

  it('adds a new joint via the form, submitting the full updated list', async () => {
    mockUpdateAssemblyJoints.mockResolvedValue({ parts: PARTS, joints: [] });
    const user = userEvent.setup();
    render(<AssemblyJointsEditor nodeId="n1" parts={PARTS} joints={[]} />);

    await user.click(screen.getByRole('button', { name: '+ Add mate/joint' }));
    await user.type(screen.getByLabelText('Name'), 'shoulder');
    await user.selectOptions(screen.getByLabelText('Base part'), 'base_link');
    await user.selectOptions(screen.getByLabelText('Follower part'), 'upper_arm');
    await user.click(screen.getByRole('button', { name: 'Add joint' }));

    expect(mockUpdateAssemblyJoints).toHaveBeenCalledWith('n1', [
      expect.objectContaining({
        name: 'shoulder',
        type: 'revolute',
        base: 'base_link',
        follower: 'upper_arm',
      }),
    ]);
  });

  it('deletes a joint by submitting the list without it', async () => {
    mockUpdateAssemblyJoints.mockResolvedValue({ parts: PARTS, joints: [] });
    const user = userEvent.setup();
    render(
      <AssemblyJointsEditor
        nodeId="n1"
        parts={PARTS}
        joints={[
          { name: 'shoulder', type: 'revolute', base: 'base_link', follower: 'upper_arm', axis: [0, 0, 1], anchor: [0, 0, 0] },
        ]}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Delete joint shoulder' }));

    expect(mockUpdateAssemblyJoints).toHaveBeenCalledWith('n1', []);
  });
});
