import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import type { CreationView } from '../../../api/endpoints/create';
import type { Readiness } from '../../../api/endpoints/dfmFindings';
import type { TwinNode } from '../../../types/twin';

const getReleaseChecklist = vi.fn();
vi.mock('../../../api/endpoints/dfmFindings', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/dfmFindings')>()),
  getReleaseChecklist: (...a: unknown[]) => getReleaseChecklist(...a),
}));
vi.mock('../MakePanel', () => ({ MakePanel: () => <div data-testid="make-panel" /> }));
vi.mock('../InspectPanel', () => ({ InspectPanel: () => <div data-testid="inspect-panel" /> }));
vi.mock('../ProductStage', () => ({
  ProductStage: ({ selectedId }: { selectedId: string | null }) => <div data-testid="product-stage" data-selected={selectedId ?? ''} />,
}));

import { MakeStage, readinessText } from '../MakeStage';

const P = 'proj-make';

function creation(builds: unknown[] = []): CreationView {
  return { readiness: { blockers: [] }, creation: { builds } } as unknown as CreationView;
}

const BUILD = {
  id: 'b1',
  number: 1,
  status: 'completed',
  route: 'home',
  process: 'FDM 3D printing',
  revision: { work_product_id: 'wp', version: 2, label: 'Bracket rev 2' },
  acceptance: null,
};

const readiness = (over: Partial<Readiness>): Readiness => ({ level: 'in_progress', label: '', prototype: false, production: false, next_steps: [], ...over });

beforeEach(() => {
  getReleaseChecklist.mockReset().mockResolvedValue({
    project_id: P,
    applies: true,
    readiness: readiness({ next_steps: ['Validate the exact model'] }),
    items: [
      { id: 'components_exact', label: 'Every component has an exact model', status: 'pass', detail: '', blockers: [], next_steps: [] },
      { id: 'dfm', label: 'Manufacturing checks pass', status: 'fail', detail: '1 blocking', blockers: [], next_steps: [] },
    ],
  });
});

describe('Make', () => {
  it('shows the release checklist and the builds behind buttons, one open at a time', async () => {
    render(<MakeStage projectId={P} view={creation([BUILD])} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    const checklist = await screen.findByRole('button', { name: /Release checklist/ });
    await waitFor(() => expect(checklist).toHaveTextContent('1 of 2 pass'));
    const builds = screen.getByRole('button', { name: /Builds/ });
    expect(builds).toHaveTextContent('1');
    expect(screen.queryByTestId('release-checklist')).toBeNull();
    expect(screen.queryByTestId('make-outputs')).toBeNull();

    await userEvent.click(checklist);
    expect(screen.getByTestId('release-checklist')).toHaveTextContent('Not ready to make yet: 1 next step');
    expect(screen.getByTestId('release-checklist')).toHaveTextContent('Manufacturing checks pass · 1 blocking');

    await userEvent.click(builds);
    expect(screen.queryByTestId('release-checklist')).toBeNull();
    expect(screen.getByTestId('make-outputs')).toHaveTextContent('Build 1');
  });

  it('has one primary action that follows the builds, and opens its detail in place', async () => {
    const { unmount } = render(<MakeStage projectId={P} view={creation()} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Plan the making');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(screen.getByTestId('make-panel')).toBeInTheDocument();
    unmount();

    render(<MakeStage projectId={P} view={creation([BUILD])} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Inspect the result');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(screen.getByTestId('inspect-panel')).toBeInTheDocument();
  });

  it('keeps each checklist icon beside its label, even for a long detail', async () => {
    const long = 'USB-C desk lamp enclosure: no exact manufacturing analysis of a validated model; '.repeat(3);
    getReleaseChecklist.mockResolvedValue({
      project_id: P,
      applies: true,
      readiness: readiness({ next_steps: ['a', 'b'] }),
      items: [
        { id: 'components_exact', label: 'Every required component has a validated exact model', status: 'fail', detail: 'x', blockers: [], next_steps: [] },
        { id: 'dfm', label: 'Manufacturing analysis passed, or its issues accepted by a person', status: 'fail', detail: long, blockers: [], next_steps: [] },
        { id: 'approvals', label: '', status: 'fail', detail: '', blockers: [], next_steps: [] },
      ],
    });
    render(<MakeStage projectId={P} view={creation()} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: /Release checklist/ }));
    const row = await screen.findByTestId('release-item-dfm');
    // One icon and one text block per row: the label and its detail sit in the
    // same block, so the text wraps beside the icon instead of under it.
    expect(row.children).toHaveLength(2);
    expect(row.children[0]!.tagName.toLowerCase()).toBe('svg');
    expect(row.children[1]).toHaveClass('cv-checklist-text');
    expect(row.children[1]).toHaveTextContent('Manufacturing analysis passed, or its issues accepted by a person · USB-C');
    // An item with no label still names itself rather than showing a bare icon.
    expect(screen.getByTestId('release-item-approvals')).toHaveTextContent('approvals');
  });

  it('lays checklist rows out without wrapping the icon onto its own line', () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../../styles/create.css'), 'utf8').replace(/\r\n/g, '\n');
    const rule = (selector: string) => {
      const at = css.indexOf(`${selector} {`);
      expect(at, selector).toBeGreaterThanOrEqual(0);
      return css.slice(at, css.indexOf('}', at));
    };
    expect(rule('.cv-checklist li')).toContain('flex-wrap: nowrap');
    expect(rule('.cv-checklist li > svg')).toContain('flex: none');
    expect(rule('.cv-checklist-text')).toContain('min-width: 0');
  });

  it('claims prototype or production readiness only when the checklist backs it', () => {
    expect(readinessText(readiness({ production: true, prototype: true }))).toBe('Ready for production');
    expect(readinessText(readiness({ prototype: true }))).toBe('Ready for prototype');
    expect(readinessText(readiness({}))).toBe('Not ready to make yet');
  });
});

describe('Make shows the model each build makes', () => {
  const node = (id: string, name: string) => ({ id, name, type: 'work_product', properties: {} }) as unknown as TwinNode;
  const ASSEMBLY = node('wp-asm', 'Smart desk lamp');
  const build = (number: number, status: string, wp: string) => ({
    ...BUILD,
    id: `b${number}`,
    number,
    status,
    revision: { work_product_id: wp, version: 2, label: `Lamp rev ${number}` },
  });

  it('shows the live build\'s own revision in the 3D view, named by the build', () => {
    render(
      <MakeStage projectId={P} view={creation([build(4, 'released', 'wp-asm')])} nodes={[ASSEMBLY]} model={undefined} modelRevision={null} onAsk={vi.fn()} />,
    );
    expect(screen.getByTestId('product-stage')).toHaveAttribute('data-selected', 'wp-asm');
    expect(screen.getByTestId('make-model')).toHaveTextContent('Build 4 · Lamp rev 4');
  });

  it('puts the build\'s model first, in a wide column', () => {
    const { container } = render(
      <MakeStage projectId={P} view={creation([build(4, 'released', 'wp-asm')])} nodes={[ASSEMBLY]} model={undefined} modelRevision={null} onAsk={vi.fn()} />,
    );
    const col = container.querySelector('.cv-stage-col');
    expect(col).toHaveClass('is-model');
    expect(col?.firstElementChild).toHaveAttribute('data-testid', 'make-model');
  });

  it('gives the model the room: a full-width column and a canvas sized to the window', () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../../styles/create-stage.css'), 'utf8').replace(/\r\n/g, '\n');
    const rule = (selector: string) => {
      const at = css.indexOf(`${selector} {`);
      expect(at, selector).toBeGreaterThanOrEqual(0);
      return css.slice(at, css.indexOf('}', at));
    };
    expect(rule('.cv-stage-col.is-model')).toContain('max-width: none');
    expect(rule('.cv-stage-col.is-model .cv-stage-canvas')).toContain('100dvh');
  });

  it('skips a cancelled build for the newest live one', () => {
    const builds = [build(3, 'released', 'wp-asm'), build(4, 'cancelled', 'wp-old')];
    render(<MakeStage projectId={P} view={creation(builds)} nodes={[ASSEMBLY, node('wp-old', 'Old')]} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.getByTestId('product-stage')).toHaveAttribute('data-selected', 'wp-asm');
    expect(screen.getByTestId('make-model')).toHaveTextContent('Build 3');
  });

  it('shows no model before there is a build', () => {
    render(<MakeStage projectId={P} view={creation()} nodes={[ASSEMBLY]} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.queryByTestId('product-stage')).not.toBeInTheDocument();
    expect(screen.queryByTestId('make-model')).not.toBeInTheDocument();
  });

  it('says so when the build\'s model is not in the twin, instead of a blank view', () => {
    render(<MakeStage projectId={P} view={creation([build(4, 'released', 'wp-gone')])} nodes={[ASSEMBLY]} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.queryByTestId('product-stage')).not.toBeInTheDocument();
    expect(screen.getByTestId('make-model')).toHaveTextContent('not in the twin');
  });
});
