import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '../../../test/test-utils';
import { MessageBody } from '../MessageBody';
import type { UiBlock } from '../../../types/ui-blocks';

describe('Markdown in a reply', () => {
  it('renders emphasis, code, lists and safe links', () => {
    const { container } = render(
      <MessageBody text={'**Bold** and `code`\n\n- one\n- two\n\n1. first\n\n[docs](https://example.com) [bad](javascript:alert(1))'} />,
    );
    expect(screen.getByText('Bold').tagName).toBe('STRONG');
    expect(screen.getByText('code').tagName).toBe('CODE');
    expect(container.querySelectorAll('ul > li')).toHaveLength(2);
    expect(container.querySelectorAll('ol > li')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'docs' })).toHaveAttribute('href', 'https://example.com');
    // A non-http link is shown as its label, never as a clickable script.
    expect(screen.queryByRole('link', { name: 'bad' })).not.toBeInTheDocument();
    expect(screen.getByText(/bad/)).toBeInTheDocument();
  });

  it('shows markup in the text as text', () => {
    const { container } = render(<MessageBody text={'<img src=x onerror=alert(1)> <b>hi</b>'} />);
    expect(container.querySelector('img, b')).toBeNull();
    expect(screen.getByText(/<b>hi<\/b>/)).toBeInTheDocument();
  });

  it("leaves the person's own message as typed", () => {
    render(<MessageBody text="**not bold**" plain />);
    expect(screen.getByText('**not bold**').tagName).toBe('P');
  });
});

describe('Blocks', () => {
  it('sends a single choice once and shows the answer', () => {
    const onReply = vi.fn();
    const blocks: UiBlock[] = [
      { type: 'choice', prompt: 'Which rail?', options: [{ label: '5V' }, { label: '3V3', value: 'Use 3V3' }] },
    ];
    render(<MessageBody text="" blocks={blocks} onReply={onReply} interactive />);
    fireEvent.click(screen.getByRole('button', { name: '3V3' }));
    expect(onReply).toHaveBeenCalledWith('Use 3V3');
    expect(screen.getByText('You answered: Use 3V3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '5V' }));
    expect(onReply).toHaveBeenCalledTimes(1);
  });

  it('collects a multiple choice before sending', () => {
    const onReply = vi.fn();
    const blocks: UiBlock[] = [
      { type: 'choice', prompt: 'Finishes?', multiple: true, options: [{ label: 'Matte' }, { label: 'Gloss' }, { label: 'Raw' }] },
    ];
    render(<MessageBody text="" blocks={blocks} onReply={onReply} interactive />);
    const send = screen.getByRole('button', { name: 'Send' });
    expect(send).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Matte/));
    fireEvent.click(screen.getByLabelText(/Raw/));
    fireEvent.click(send);
    expect(onReply).toHaveBeenCalledWith('Matte, Raw');
  });

  it('a choice on an older reply cannot be answered', () => {
    const blocks: UiBlock[] = [{ type: 'choice', prompt: 'Which?', options: [{ label: 'A' }, { label: 'B' }] }];
    render(<MessageBody text="" blocks={blocks} onReply={vi.fn()} interactive={false} />);
    expect(screen.getByRole('button', { name: 'A' })).toBeDisabled();
  });

  it('asks for a measurement and sends it with its unit', () => {
    const onReply = vi.fn();
    const blocks: UiBlock[] = [{ type: 'measurement_request', label: 'Desk thickness', unit: 'mm', critical: true }];
    render(<MessageBody text="" blocks={blocks} onReply={onReply} interactive />);
    const input = screen.getByLabelText('Desk thickness in mm');
    const send = screen.getByRole('button', { name: 'Send' });
    fireEvent.change(input, { target: { value: 'abc' } });
    expect(send).toBeDisabled();
    fireEvent.change(input, { target: { value: '25.4' } });
    fireEvent.click(send);
    expect(onReply).toHaveBeenCalledWith('Desk thickness: 25.4 mm (measured by me)');
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });

  it('marks a figure over its limit and says how it was arrived at', () => {
    const blocks: UiBlock[] = [
      {
        type: 'metrics',
        title: 'Rail',
        basis: 'estimate',
        items: [
          { label: 'Draw', value: 2.61, unit: 'A', limit: 2.4 },
          { label: 'Clearance', value: 1.2, unit: 'mm', limit: 1.0, limit_kind: 'min' },
        ],
      },
    ];
    render(<MessageBody text="" blocks={blocks} />);
    expect(screen.getByText('Estimate')).toBeInTheDocument();
    expect(screen.getAllByText('Over its limit')).toHaveLength(1);
    expect(screen.queryByText('Below its minimum')).not.toBeInTheDocument();
  });

  it('renders a table, a comparison, a callout and a part', () => {
    const onShowPart = vi.fn();
    const blocks: UiBlock[] = [
      { type: 'table', title: 'BOM', columns: ['Part', 'Qty'], rows: [['Screw', 4], ['Nut', null]] },
      {
        type: 'comparison',
        criteria: ['Cost'],
        options: [
          { name: 'PLA', values: { Cost: '£2' }, recommended: true, pros: ['Cheap'] },
          { name: 'PETG', cons: ['Strings'] },
        ],
      },
      { type: 'callout', tone: 'risk', title: 'Over budget', body: 'By 8.8%' },
      { type: 'part', node_id: 'n1', label: 'enclosure.step' },
    ];
    render(<MessageBody text="" blocks={blocks} onShowPart={onShowPart} />);
    expect(screen.getByRole('columnheader', { name: 'Qty' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '–' })).toBeInTheDocument();
    expect(screen.getByText('Recommended')).toBeInTheDocument();
    expect(screen.getByText('£2')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent('Over budget');
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    expect(onShowPart).toHaveBeenCalledWith('n1');
  });

  it('says so when a block is of a kind this version does not know', () => {
    render(<MessageBody text="" blocks={[{ type: 'hologram' } as unknown as UiBlock]} />);
    expect(screen.getByText(/cannot show/)).toBeInTheDocument();
  });
});
