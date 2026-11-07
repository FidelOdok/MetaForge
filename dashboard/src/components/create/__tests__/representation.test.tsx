import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import { componentOfObject, useComponentSelection } from '../../../store/component-selection-store';
import type { RepresentationView, TwinNode } from '../../../types/twin';
import { guardedLabel, representationText } from '../../geometry/RepresentationBadge';
import { pickedComponent } from '../../preview/engines/ModelPreview';
import { currentRevisions, displayRank, levelText, preferredDisplayNode, representationOf } from '../stage-logic';

const makeStructuredMesh = vi.fn();
vi.mock('../../../api/endpoints/representation', () => ({
  makeStructuredMesh: (...a: unknown[]) => makeStructuredMesh(...a),
}));
vi.mock('../../../api/endpoints/reconstruction', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/reconstruction')>()),
  getReconstruction: vi.fn().mockResolvedValue({ concept_accepted: false, accepted: false, components: [], summary: '' }),
}));
vi.mock('../../../hooks/use-twin', async (orig) => ({
  ...(await orig<typeof import('../../../hooks/use-twin')>()),
  useNodeVersionHistory: () => ({ data: undefined }),
}));
vi.mock('../../preview/PreviewHost', () => ({
  PreviewHost: ({ node }: { node: TwinNode }) => <div data-testid="preview-host">{node.name}</div>,
}));

import { ProductStage } from '../ProductStage';
import { DevelopStage } from '../DevelopStage';

const P = 'proj-rep';

function rep(level: RepresentationView['level'], extra: Partial<RepresentationView> = {}): RepresentationView {
  const ranks = { L3: 5, L2: 4, L1: 3, L0: 2, illustration_image: 1 } as const;
  const labels = { L3: 'Validated', L2: 'Exact', L1: 'Preview', L0: 'Illustrative', illustration_image: 'Illustrative' };
  return {
    level,
    authority: level === 'L1' ? 'structured' : 'illustrative',
    label: labels[level],
    source_revision: null,
    component_ids: [],
    generated_by: {},
    derived: true,
    display_rank: ranks[level],
    ...extra,
  };
}

function node(id: string, props: Record<string, string | number | boolean>, extra: Partial<TwinNode> = {}, updatedAt = '2026-11-05T10:00:00Z'): TwinNode {
  return { id, name: id, type: 'work_product', domain: '', status: '', properties: props, updatedAt, ...extra } as TwinNode;
}

const IMAGE = node('Concept image', { wp_type: 'image', format: 'png' }, { representation: rep('illustration_image') }, '2026-11-05T12:00:00Z');
const L0 = node('AI concept', { wp_type: 'render', format: 'glb', generated_by: 'twin.generate_concept_mesh' }, { representation: rep('L0') }, '2026-11-05T11:00:00Z');
const L1 = node(
  'Simple parts',
  { wp_type: 'render', format: 'glb', generated_by: 'twin.render_structured_mesh' },
  { representation: rep('L1', { source_revision: 'SKT-LAMP@2', component_ids: ['CMP-base', 'CMP-arm'] }) },
  '2026-11-05T09:00:00Z',
);
const L2 = node(
  'Base CAD',
  { wp_type: 'cad_model', format: 'step', item_key: 'CAD-BASE', item_revision: 2 },
  { geometryState: { state: 'exact_ready' } as TwinNode['geometryState'], representation: rep('L2', { authority: 'exact', label: 'Exact', source_revision: 'CAD-BASE@2' }) },
  '2026-11-05T08:00:00Z',
);
const L3 = node(
  'Base CAD validated',
  { wp_type: 'cad_model', format: 'step', item_key: 'CAD-BASE', item_revision: 1 },
  { geometryState: { state: 'validated' } as TwinNode['geometryState'], representation: rep('L3', { authority: 'validated', label: 'Validated', source_revision: 'CAD-BASE@1' }) },
  '2026-11-05T07:00:00Z',
);

describe('display preference (SYNC-010)', () => {
  it('prefers L3, then L2, L1, L0, then a picture, whatever is newest', () => {
    const lid = node('Lid', { wp_type: 'cad_model', format: 'step', item_key: 'CAD-LID', item_revision: 1 }, {
      geometryState: { state: 'validated' } as TwinNode['geometryState'],
      representation: rep('L3', { authority: 'validated', label: 'Validated' }),
    });
    expect(preferredDisplayNode([IMAGE, L0, L1, L2, lid])?.id).toBe('Lid');
    expect(preferredDisplayNode([IMAGE, L0, L1, L2])?.id).toBe('Base CAD');
    expect(preferredDisplayNode([IMAGE, L0, L1])?.id).toBe('Simple parts');
    expect(preferredDisplayNode([IMAGE, L0])?.id).toBe('AI concept');
    expect(preferredDisplayNode([IMAGE])?.id).toBe('Concept image');
  });

  it('shows the current revision of an item, not a validated older one', () => {
    expect(currentRevisions([L3, L2]).map((n) => n.id)).toEqual(['Base CAD']);
    expect(preferredDisplayNode([L3, L2, L1])?.id).toBe('Base CAD');
  });

  it('ranks an out of date revision last and falls back to the type without a representation', () => {
    const stale = { ...L2, id: 'stale', properties: { ...L2.properties, item_key: 'CAD-X' }, geometryState: { state: 'stale' } as TwinNode['geometryState'] };
    expect(displayRank(stale)).toBe(0);
    expect(displayRank(node('old cad', { wp_type: 'cad_model', format: 'step' }))).toBe(4);
    expect(displayRank(node('old mesh', { wp_type: 'render', format: 'glb', generated_by: 'twin.render_structured_mesh' }))).toBe(3);
  });
});

describe('labels', () => {
  it('names each level with its maturity and source revision', () => {
    expect(representationOf(L1)).toEqual({ maturity: 'Preview', status: '3D Preview · simple parts, sizes estimated', kind: 'preview' });
    expect(levelText(L1)).toBe('L1 · simple parts, sizes estimated · from SKT-LAMP@2');
    expect(levelText(L3)).toBe('L3 · from the validated exact model · from CAD-BASE@1');
    expect(representationOf(L3).maturity).toBe('Validated');
    expect(representationOf(L2).status).toBe('Engineering Geometry · exact model available');
    expect(representationOf(L0).status).toBe('3D Concept · illustrative');
    expect(representationText(rep('L2', { authority: 'preview' }))).toBe('L2 · preview, not exact');
  });

  it('never labels L0 or L1 exact or validated, whatever the record says (the label guard)', () => {
    const lying0 = { ...L0, representation: rep('L0', { label: 'Validated', authority: 'validated' }) };
    const lying1 = { ...L1, properties: { wp_type: 'cad_model', format: 'step' }, geometryState: { state: 'validated' } as TwinNode['geometryState'] };
    expect(guardedLabel('L0', 'Validated')).toBe('Illustrative');
    expect(guardedLabel('L1', 'Exact')).toBe('Preview');
    expect(guardedLabel('L3', 'Validated')).toBe('Validated');
    expect(representationOf(lying0).maturity).toBe('Illustrative');
    const shown = representationOf(lying1);
    expect(shown.maturity).toBe('Preview');
    expect(shown.status).not.toMatch(/validated/i);
  });
});

describe('selecting a component', () => {
  it('finds the component id in a picked mesh or its parents', () => {
    const root = { name: 'metaforge_structured_mesh', userData: { level: 'L1' }, parent: null };
    const partNode = { name: 'CMP-arm', userData: { component_id: 'CMP-arm', part_name: 'Arm' }, parent: root };
    const mesh = { name: 'Arm_mesh', userData: {}, parent: partNode };
    expect(componentOfObject(mesh)).toEqual({ componentId: 'CMP-arm', name: 'Arm' });
    expect(componentOfObject(root)).toBeNull();
  });

  it('falls back to a single-component node, and picks nothing on an AI concept', () => {
    const cad = node('Base', { wp_type: 'cad_model' }, { representation: rep('L2', { component_ids: ['CMP-base'] }) });
    expect(pickedComponent({ userData: {} }, cad)).toEqual({ componentId: 'CMP-base', name: 'Base' });
    expect(pickedComponent({ userData: {} }, L0)).toBeNull();
  });
});

describe('the Develop stage', () => {
  beforeEach(() => {
    useComponentSelection.setState({ selected: {} });
    makeStructuredMesh.mockReset();
  });

  function develop(nodes: TwinNode[], onSelect = vi.fn()) {
    render(
      <DevelopStage
        projectId={P}
        nodes={nodes}
        view={undefined}
        selectedId={null}
        onSelect={onSelect}
        working={false}
        engineeringChecks={null}
        onAsk={vi.fn()}
        onSketchFix={vi.fn()}
      />,
    );
  }

  it('shows the best representation with its level', () => {
    render(<ProductStage projectId={P} nodes={[IMAGE, L0, L1]} selectedId={null} working={false} />);
    expect(screen.getByTestId('preview-host')).toHaveTextContent('Simple parts');
    expect(screen.getByTestId('stage-maturity')).toHaveTextContent('Preview');
    expect(screen.getByTestId('stage-level')).toHaveTextContent('L1 · simple parts, sizes estimated');
  });

  it('never repeats a manufacturing note on the model label (Check owns manufacturing results)', () => {
    const lying = { ...L1, properties: { ...L1.properties, manufacturing_note: 'Verified production ready' } };
    render(<ProductStage projectId={P} nodes={[lying]} selectedId={null} working={false} />);
    expect(screen.queryByTestId('stage-mfg-note')).toBeNull();
    expect(screen.getByTestId('stage-status')).not.toHaveTextContent(/verified|production/i);
  });

  it('offers Show as simple 3D parts in More, and shows the result', async () => {
    const onSelect = vi.fn();
    makeStructuredMesh.mockResolvedValue({ node_id: 'Simple parts', reused: false });
    develop([IMAGE], onSelect);
    await userEvent.click(screen.getByRole('button', { name: /More/ }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Show as simple 3D parts' }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('Simple parts'));
    expect(makeStructuredMesh).toHaveBeenCalledWith(P);
  });

  it('says plainly when there is no layout to build from', async () => {
    makeStructuredMesh.mockRejectedValue({ response: { data: { detail: 'there is no concept block layout yet' } } });
    develop([IMAGE]);
    await userEvent.click(screen.getByRole('button', { name: /More/ }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Show as simple 3D parts' }));
    expect(await screen.findByTestId('simple-parts-notice')).toHaveTextContent('no concept block layout');
  });
});
