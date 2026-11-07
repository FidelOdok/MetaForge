import { describe, it, expect } from 'vitest';
import type { CreationView } from '../../../api/endpoints/create';
import type { TwinNode } from '../../../types/twin';
import { commitmentOf } from '../copy';
import { checksFromCriteria } from '../InspectPanel';
import { recommendProcess } from '../MakePanel';
import { nameFromPrompt } from '../CreationsHome';
import { defaultStageNode, isEngineering, isVisual } from '../ProductStage';

function view(over: Partial<CreationView['readiness']> = {}, items: CreationView['creation']['brief']['items'] = {}): CreationView {
  return {
    brief_keys: [],
    readiness: {
      stage: 'concept',
      stage_index: 0,
      blockers: [],
      next_action: '',
      has_concept: false,
      has_engineering_model: false,
      ...over,
    },
    creation: {
      project_id: 'p',
      version: 1,
      brief: { items, measurements: [], assumptions: [], references: [] },
      route: { route: null, process: '', machine_id: null, material: '', supplier: '', note: '' },
      inspection_plan: [],
      builds: [],
      history: [],
      updated_at: '',
    },
  };
}

function node(id: string, wpType: string, updatedAt: string): TwinNode {
  return { id, name: id, type: 'work_product', domain: '', status: '', properties: { wp_type: wpType }, updatedAt } as TwinNode;
}

describe('commitmentOf', () => {
  it('keeps spending, machines and file sharing apart from design approval', () => {
    expect(commitmentOf('tool_call', { tool: 'tripo.image_to_3d' })).toBe('spending');
    expect(commitmentOf('tool_call', { tool: 'octoprint.start_job' })).toBe('machine');
    expect(commitmentOf('upload', {})).toBe('files');
    expect(commitmentOf('design_change', {})).toBe('design');
    expect(commitmentOf('gate', {})).toBe('process');
  });
});

describe('the stage', () => {
  it('prefers the newest dimensioned model over a newer concept image', () => {
    const nodes = [node('img', 'image', '2026-11-04T10:00:00Z'), node('cad', 'cad_model', '2026-11-03T10:00:00Z')];
    expect(defaultStageNode(nodes)?.id).toBe('cad');
    expect(isEngineering(nodes[0]!)).toBe(false);
  });

  it('never puts a saved freehand sketch on stage as a result (it lives on the sketch layer)', () => {
    const sketch = node('skt', 'sketch_strokes', '2026-11-05T10:00:00Z');
    expect(isVisual(sketch)).toBe(false);
    expect(defaultStageNode([sketch, node('img', 'image', '2026-11-04T10:00:00Z')])?.id).toBe('img');
  });
});

describe('plans and suggestions', () => {
  it('turns each success criterion into a required, person-reviewed check', () => {
    const checks = checksFromCriteria('- Holds 400 g\nSlides on by hand\n\n', 'photo from the side');
    expect(checks.map((c) => c.feature)).toEqual(['Holds 400 g', 'Slides on by hand']);
    expect(checks[0]!.method).toBe('measurement');
    expect(checks[1]!.method).toBe('camera');
    expect(checks.every((c) => c.blocking && c.reviewer === 'person')).toBe(true);
  });

  it('suggests a process and says why', () => {
    const metal = view({}, { materials: { value: '6061 aluminium', status: 'confirmed', source: 'user' } });
    expect(recommendProcess(metal, node('m', 'cad_model', ''))?.process).toBe('CNC machining');
    expect(recommendProcess(view(), node('m', 'cad_model', ''))?.process).toBe('FDM 3D printing');
    expect(recommendProcess(view(), undefined)).toBeNull();
  });

  it('names a creation from the first words of the request', () => {
    expect(nameFromPrompt('a hook that clamps under my desk and holds headphones.')).toBe('A hook that clamps under my');
  });
});
