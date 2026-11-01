import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '../../../test/test-utils';

// The real mermaid needs layout to measure labels, which jsdom does not do.
// What matters here is the wiring: that a mermaid fence reaches it at all,
// that its output is what the reader sees, and that a diagram it rejects
// falls back to the source instead of vanishing.
const renderDiagram = vi.fn();
const initialize = vi.fn();
vi.mock('mermaid', () => ({
  default: {
    initialize: (...args: unknown[]) => initialize(...args),
    render: (...args: unknown[]) => renderDiagram(...args),
  },
}));

import { MarkdownView } from '../engines/DocumentEngines';

const DIAGRAM = 'flowchart LR\n    a[One] --> b[Two]';

describe('mermaid in a previewed document', () => {
  beforeEach(() => {
    renderDiagram.mockReset();
    initialize.mockReset();
    renderDiagram.mockResolvedValue({ svg: '<svg data-testid="drawn"></svg>' });
  });

  it('draws the diagram instead of printing its source', async () => {
    render(<MarkdownView source={`# Design\n\n\`\`\`mermaid\n${DIAGRAM}\n\`\`\`\n`} />);

    const diagram = await screen.findByTestId('preview-mermaid');
    await waitFor(() => expect(diagram.dataset.state).toBe('drawn'));
    expect(diagram.querySelector('svg')).not.toBeNull();
    expect(diagram.textContent).not.toContain('flowchart LR');
  });

  it('hands mermaid the fence body, not the fence', async () => {
    render(<MarkdownView source={`\`\`\`mermaid\n${DIAGRAM}\n\`\`\`\n`} />);

    await waitFor(() => expect(renderDiagram).toHaveBeenCalled());
    expect(renderDiagram.mock.calls[0]?.[1]).toBe(DIAGRAM);
  });

  // Work product text is written by agents. Mermaid's 'loose' mode would put
  // their markup into the page; 'strict' keeps the output mermaid's own.
  it('renders with the sanitising security level', async () => {
    render(<MarkdownView source={`\`\`\`mermaid\n${DIAGRAM}\n\`\`\`\n`} />);

    await waitFor(() => expect(initialize).toHaveBeenCalled());
    expect(initialize.mock.calls[0]?.[0]).toMatchObject({ securityLevel: 'strict' });
  });

  it('falls back to the source when the diagram will not parse', async () => {
    renderDiagram.mockRejectedValue(new Error('Parse error on line 2'));
    render(<MarkdownView source={'```mermaid\nflowchart LR\n  a -->\n```\n'} />);

    const diagram = await screen.findByTestId('preview-mermaid');
    await waitFor(() => expect(diagram.dataset.state).toBe('failed'));
    expect(diagram.textContent).toContain('could not be drawn');
    expect(diagram.querySelector('pre')?.textContent).toContain('flowchart LR');
  });

  it('leaves every other fenced block a code block', async () => {
    render(<MarkdownView source={'```python\nprint("hello")\n```\n'} />);

    expect(screen.queryByTestId('preview-mermaid')).toBeNull();
    expect(screen.getByText('print("hello")').tagName).toBe('PRE');
    expect(renderDiagram).not.toHaveBeenCalled();
  });
});
