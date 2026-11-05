import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/dfmFindings';
import type { Readiness, ReleaseChecklist } from '../../../api/endpoints/dfmFindings';
import { ReadinessStatus, readinessText } from '../ReadinessStatus';

const P = 'p1';

function readiness(over: Partial<Readiness> = {}): Readiness {
  return { level: 'in_progress', label: 'Not ready to make yet', prototype: false, production: false, next_steps: [], ...over };
}

function checklist(r: Readiness, applies = true): ReleaseChecklist {
  return { project_id: P, applies, readiness: r, items: [], blockers: [], releasable: r.production };
}

describe('Create readiness wording (no fake completion)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('says ready only when the checklist backs it', () => {
    expect(readinessText(readiness({ production: true, prototype: true, level: 'production' }))).toBe('Ready for production');
    expect(readinessText(readiness({ prototype: true, level: 'prototype' }))).toBe('Ready for prototype');
    expect(readinessText(readiness({ next_steps: ['a', 'b'] }))).toBe('Not ready to make yet: 2 next steps');
    expect(readinessText(readiness())).not.toMatch(/ready for|%/i);
  });

  it('is hidden until a process with exact checks applies', async () => {
    const get = vi.spyOn(api, 'getReleaseChecklist').mockResolvedValue(checklist(readiness(), false));
    const { container } = render(<ReadinessStatus projectId={P} />);
    await vi.waitFor(() => expect(get).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="readiness-status"]')).toBeNull();
  });

  it('lists the missing items as next steps in section 10 wording', async () => {
    vi.spyOn(api, 'getReleaseChecklist').mockResolvedValue(
      checklist(
        readiness({
          next_steps: [
            'Engineering Update: validation pending for Lid',
            'Manufacturing analysis pending for Enclosure',
            'Physical or supplier confirmation required: Fit test of a printed sample (Enclosure)',
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    render(<ReadinessStatus projectId={P} />);
    const chip = await screen.findByRole('button', { name: /Not ready to make yet: 3 next steps/ });
    expect(screen.queryByText(/Ready for/)).not.toBeInTheDocument();
    await user.click(chip);
    const list = await screen.findByTestId('readiness-next-steps');
    expect(list).toHaveTextContent('Engineering Update: validation pending for Lid');
    expect(list).toHaveTextContent('Physical or supplier confirmation required');
  });

  it('shows ready for prototype with what production still needs', async () => {
    vi.spyOn(api, 'getReleaseChecklist').mockResolvedValue(
      checklist(readiness({ level: 'prototype', prototype: true, next_steps: ['Ask a reviewer to approve the release'] })),
    );
    const user = userEvent.setup();
    render(<ReadinessStatus projectId={P} />);
    await user.click(await screen.findByRole('button', { name: /Ready for prototype/ }));
    expect(screen.getByText('Before production:')).toBeInTheDocument();
    expect(screen.queryByText(/Ready for production/)).not.toBeInTheDocument();
  });
});
