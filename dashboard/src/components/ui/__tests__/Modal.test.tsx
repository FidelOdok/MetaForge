import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '../../../test/test-utils';
import { ConfirmDialog, Modal } from '../Modal';
import { PageHeader } from '../PageHeader';

describe('Modal', () => {
  it('shows its title, body and footer only while open', () => {
    const { rerender } = render(
      <Modal open={false} onClose={vi.fn()} title="Rename project" footer={<button>Save</button>}>
        body text
      </Modal>,
    );
    expect(screen.queryByText('body text')).not.toBeInTheDocument();
    rerender(
      <Modal open onClose={vi.fn()} title="Rename project" footer={<button>Save</button>}>
        body text
      </Modal>,
    );
    expect(screen.getByRole('heading', { name: 'Rename project' })).toBeInTheDocument();
    expect(screen.getByText('body text')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('closes from the X and on Escape', () => {
    const onClose = vi.fn();
    render(<Modal open onClose={onClose} title="T" />);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    const dialog = screen.getByText('T').closest('dialog')!;
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('submits through the form', () => {
    const onSubmit = vi.fn();
    render(
      <Modal open onClose={vi.fn()} title="T" onSubmit={onSubmit} footer={<button type="submit">Go</button>} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(onSubmit).toHaveBeenCalled();
  });
});

describe('ConfirmDialog', () => {
  it('puts Cancel first and the action last', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open title="Delete project" confirmLabel="Delete" tone="danger" onConfirm={onConfirm} onCancel={onCancel}>
        This cannot be undone.
      </ConfirmDialog>,
    );
    const buttons = screen.getAllByRole('button').map((b) => b.textContent);
    expect(buttons.slice(-2)).toEqual(['Cancel', 'Delete']);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onConfirm).toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
  });
});

describe('PageHeader', () => {
  it('renders title, summary, back link and actions', () => {
    render(
      <PageHeader title="Runs" meta="3 runs" back={{ to: '/projects', label: 'Projects' }} actions={<button>Refresh</button>} />,
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Runs' })).toBeInTheDocument();
    expect(screen.getByText('3 runs')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/projects');
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
  });
});
