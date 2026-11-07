import { describe, expect, it } from 'vitest';
import type { CreationView } from '../../../api/endpoints/create';
import type { TwinNode } from '../../../types/twin';
import { MILESTONES, milestoneIndex, milestoneSummaries } from '../copy';
import {
  DEVELOP_LABEL,
  NOT_ASSESSED,
  developPrimary,
  manufacturingNote,
  overclaims,
  representationOf,
  truthfulLabel,
  type DevelopFacts,
} from '../stage-logic';

const NONE: DevelopFacts = {
  hasEngineering: false,
  hasParametricSketch: false,
  conceptAccepted: false,
  editableFromConcept: false,
};

function node(props: Record<string, string | number | boolean>, extra: Partial<TwinNode> = {}): TwinNode {
  return { id: 'n', name: 'n', type: 'work_product', domain: '', status: '', properties: props, updatedAt: '', ...extra } as TwinNode;
}

describe('developPrimary', () => {
  it('asks for the exact model when nothing dimensioned exists yet', () => {
    expect(developPrimary(NONE)).toBe('request_exact');
  });

  it('follows the twin: make editable, build exact, then describe a change', () => {
    expect(developPrimary({ ...NONE, conceptAccepted: true })).toBe('reconstruct');
    expect(developPrimary({ ...NONE, conceptAccepted: true, editableFromConcept: true })).toBe('request_exact');
    expect(developPrimary({ ...NONE, hasParametricSketch: true, conceptAccepted: true })).toBe('build_exact');
    expect(developPrimary({ ...NONE, hasEngineering: true, hasParametricSketch: true })).toBe('describe_change');
  });

  it('labels each action in plain words', () => {
    expect(DEVELOP_LABEL).toEqual({
      build_exact: 'Build exact model',
      reconstruct: 'Make it editable',
      request_exact: 'Ask for the exact model',
      describe_change: 'Describe a change',
    });
  });
});

describe('representationOf', () => {
  it('says a concept mesh is an illustrative 3D concept', () => {
    const mesh = node({ wp_type: 'render', format: 'glb', generated_by: 'twin.generate_concept_mesh', authority: 'illustrative' });
    expect(representationOf(mesh)).toEqual({ maturity: 'Illustrative', status: '3D Concept · illustrative', kind: 'concept3d' });
  });

  it('says an image is a concept, and never more', () => {
    expect(representationOf(node({ wp_type: 'image', format: 'png' }))).toMatchObject({
      maturity: 'Illustrative',
      status: 'Concept · illustrative',
    });
  });

  it('reads CAD state into Preview, Exact, Validated and the failure wording', () => {
    const cad = (state?: string) =>
      node({ wp_type: 'cad_model', format: 'step' }, state ? { geometryState: { state } as TwinNode['geometryState'] } : {});
    expect(representationOf(cad('preview')).status).toBe('3D Preview · provisional');
    expect(representationOf(cad('exact_ready')).status).toBe('Engineering Geometry · exact model available');
    expect(representationOf(cad('exact_ready'), 'running').status).toBe('Engineering Update · validation pending');
    expect(representationOf(cad('validated')).maturity).toBe('Validated');
    expect(representationOf(cad('exact_ready'), 'failed').status).toBe('Preview available · Engineering update needs attention');
  });
});

describe('the label guard', () => {
  it('never lets an illustrative or provisional item claim verification or production readiness', () => {
    for (const claim of ['Verified design', 'Production-ready', 'production ready', 'Validated shape', 'Ready for production']) {
      expect(overclaims(claim, 'Illustrative')).toBe(true);
      expect(overclaims(claim, 'Preview')).toBe(true);
      expect(truthfulLabel(claim, 'Illustrative', 'Concept · illustrative')).toBe('Concept · illustrative');
    }
    expect(overclaims('Concept · illustrative, not a manufacturable model', 'Illustrative')).toBe(false);
    expect(overclaims('Engineering Geometry · validated', 'Validated')).toBe(false);
  });

  it('every illustrative status Create shows passes the guard', () => {
    const shown = [
      representationOf(node({ wp_type: 'image', format: 'png' })),
      representationOf(node({ wp_type: 'render', format: 'glb', generated_by: 'twin.generate_concept_mesh' })),
    ];
    for (const r of shown) expect(overclaims(r.status, r.maturity)).toBe(false);
  });

  it('shows a concept card manufacturing note when present, otherwise says it was not assessed', () => {
    expect(manufacturingNote('Likely printable with supports (preliminary)')).toBe('Likely printable with supports (preliminary)');
    expect(manufacturingNote(undefined)).toBe(NOT_ASSESSED);
    expect(manufacturingNote('  ')).toBe(NOT_ASSESSED);
    expect(manufacturingNote('Verified for injection moulding')).toBe(NOT_ASSESSED);
  });
});

function view(stage: CreationView['readiness']['stage'], over: Partial<CreationView['readiness']> = {}): CreationView {
  return {
    brief_keys: [],
    readiness: { stage, stage_index: 0, blockers: [], next_action: '', has_concept: false, has_engineering_model: false, ...over },
    creation: {
      project_id: 'p',
      version: 1,
      brief: { items: {}, measurements: [], assumptions: [], references: [] },
      route: { route: null, process: '', machine_id: null, material: '', supplier: '', note: '' },
      inspection_plan: [],
      builds: [],
      history: [],
      updated_at: '',
    },
  } as CreationView;
}

describe('milestones', () => {
  it('are Imagine, Develop, Check and Make', () => {
    expect([...MILESTONES]).toEqual(['Imagine', 'Develop', 'Check', 'Make']);
  });

  it('summarise the existing stages without new gates', () => {
    expect(milestoneIndex(view('concept'))).toBe(0);
    expect(milestoneIndex(view('design_in_development'))).toBe(1);
    expect(milestoneIndex(view('checked_design'))).toBe(2);
    expect(milestoneIndex(view('released_for_making'))).toBe(3);
    expect(milestoneIndex(view('build_completed'))).toBe(3);
    expect(milestoneIndex(view('accepted_product'))).toBe(4);
    const s = milestoneSummaries(view('design_in_development', { has_concept: true, has_engineering_model: true, blockers: [{ id: 'x', message: '', action: '' }] }));
    expect(s).toEqual({ Imagine: 'Concept captured', Develop: 'Dimensioned model', Check: '1 to resolve', Make: 'Not yet' });
  });
});
