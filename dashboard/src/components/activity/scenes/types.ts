import type { OperationDomain, OperationState } from '../../../types/operations';

/**
 * The visual state of an activity scene (ASVG-002). Derived from the
 * operation contract's state, never from a timer.
 */
export type SceneState = 'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'stale';

export function sceneStateOf(state: OperationState): SceneState {
  switch (state) {
    case 'queued':
      return 'queued';
    case 'running':
      return 'running';
    case 'waiting_for_user':
    case 'blocked':
      return 'waiting';
    case 'completed':
      return 'completed';
    case 'failed':
    case 'cancelled':
      return 'failed';
    case 'stale':
    case 'superseded':
      return 'stale';
  }
}

/**
 * Everything a scene may depend on. A scene is a pure function of these
 * props: no clocks, no fetches, no model-generated markup (ASVG-013).
 */
export interface SceneProps {
  state: SceneState;
  /** How many verified stages or milestones the operation has reached. */
  stageIndex: number;
  reducedMotion: boolean;
  paused: boolean;
  /** The revision a stale scene belongs to, for its label. */
  staleRevision?: string | null;
  /**
   * The design dependency graph (FORGE-793), for the cross-domain scene. When
   * absent or empty the scene stays illustrative and says so.
   */
  graph?: SceneGraph | null;
}

/** One item in the dependency graph a cross-domain scene draws. */
export interface SceneGraphNode {
  id: string;
  label: string;
  domain: string;
  /** `changed` is the item that moved; `impacted` and `possible` what it reaches. */
  role: 'changed' | 'impacted' | 'possible' | 'unaffected' | 'record' | 'idle';
}

/** One interface edge between two nodes. */
export interface SceneGraphLink {
  source: string;
  target: string;
  /** The change travels along it. */
  impacted: boolean;
  /** An inferred link nobody has confirmed yet. */
  proposed: boolean;
}

export interface SceneGraph {
  nodes: SceneGraphNode[];
  links: SceneGraphLink[];
}

export type SceneDomain = 'sketch' | 'cad' | 'eda' | 'firmware' | 'simulation' | 'manufacturing' | 'cross_domain';

export function sceneDomainOf(domain: OperationDomain): SceneDomain {
  switch (domain) {
    case 'concept':
    case 'sketch':
      return 'sketch';
    case 'mechanical':
      return 'cad';
    case 'electronics':
      return 'eda';
    case 'firmware':
      return 'firmware';
    case 'simulation':
      return 'simulation';
    case 'manufacturing':
      return 'manufacturing';
    default:
      return 'cross_domain';
  }
}

/** The class of an element that is drawn solid once its stage is reached. */
export function cls(base: string, reached: boolean): string {
  return reached ? base + ' is-reached' : base;
}
