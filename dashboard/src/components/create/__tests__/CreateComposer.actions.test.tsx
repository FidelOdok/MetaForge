import { describe, it, expect, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import { CreateComposer } from '../CreateComposer';
import { useCreateStore } from '../../../store/create-store';

const TOOLS = { ask: [], change: ['twin.propose_change'], design: ['cadquery.create'] };

describe('CreateComposer actions', () => {
  beforeEach(() => useCreateStore.setState({ sessions: {} }));

  it('fills the message and switches to Design, and sends nothing', async () => {
    const user = userEvent.setup();
    render(<CreateComposer projectId="p1" tools={TOOLS} busy={false} />);
    await user.click(screen.getByRole('button', { name: /Actions/ }));
    await user.click(screen.getByRole('button', { name: /Review the gate/ }));
    expect(screen.getByLabelText('Describe your product or ask for a change')).toHaveValue(
      'Is this product ready to move to the next stage? List what is missing.',
    );
    expect(screen.getByRole('radio', { name: 'Design' })).toHaveAttribute('aria-checked', 'true');
    expect(useCreateStore.getState().session('p1').queue).toHaveLength(0);
    expect(screen.queryByRole('group', { name: 'Actions' })).not.toBeInTheDocument();
  });

  it('shows one panel at a time', async () => {
    const user = userEvent.setup();
    render(<CreateComposer projectId="p1" tools={TOOLS} busy={false} />);
    await user.click(screen.getByRole('button', { name: /Actions/ }));
    expect(screen.getByRole('group', { name: 'Actions' })).toBeInTheDocument();
    await user.click(screen.getByTitle('Choose the model for your messages'));
    expect(screen.queryByRole('group', { name: 'Actions' })).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Model' })).toBeInTheDocument();
  });
});
