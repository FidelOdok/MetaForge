import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '../../../test/test-utils';
import { Panel, PanelLoading, SearchField } from '../Panel';
import { EmptyState } from '../EmptyState';

describe('Panel', () => {
  it('shows its label, count, actions and body', () => {
    render(
      <Panel title="Load cases" meta={3} actions={<button>New</button>} testId="p">
        body
      </Panel>,
    );
    expect(screen.getByRole('heading', { name: 'Load cases' })).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New' })).toBeInTheDocument();
    expect(screen.getByTestId('p')).toHaveTextContent('body');
  });

  it('shows a zero count as a count, not as stray text', () => {
    render(<Panel title="Results" meta={0} />);
    expect(screen.getByText('0')).toHaveClass('mf-panel-meta');
  });

  it('leaves out the meta and body when there are none', () => {
    const { container } = render(<Panel title="T" meta={false} />);
    expect(container.querySelector('.mf-panel-meta')).toBeNull();
    expect(container.querySelector('.mf-panel-body')).toBeNull();
  });
});

describe('SearchField', () => {
  it('reports what is typed', () => {
    const onChange = vi.fn();
    render(<SearchField value="" onChange={onChange} placeholder="Search requirements" />);
    fireEvent.change(screen.getByLabelText('Search requirements'), { target: { value: 'mass' } });
    expect(onChange).toHaveBeenCalledWith('mass');
  });
});

describe('PanelLoading and EmptyState', () => {
  it('announce loading and say what is missing', () => {
    render(
      <>
        <PanelLoading />
        <EmptyState title="No load cases" description="Define one." />
      </>,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(screen.getByRole('heading', { name: 'No load cases' })).toBeInTheDocument();
    expect(screen.getByText('Define one.')).toBeInTheDocument();
  });
});
