import type { ComponentType } from 'react';
import { CadScene } from './CadScene';
import { CrossDomainScene } from './CrossDomainScene';
import { EdaScene } from './EdaScene';
import { FirmwareScene } from './FirmwareScene';
import { ManufacturingScene } from './ManufacturingScene';
import { SimulationScene } from './SimulationScene';
import { SketchScene } from './SketchScene';
import { sceneDomainOf, type SceneDomain, type SceneProps } from './types';
import type { OperationDomain } from '../../../types/operations';

/**
 * The only scenes the overlay can draw (ASVG-013). Each is a predefined,
 * typed component; nothing renders SVG markup that came from a model.
 */
export const SCENES: Record<SceneDomain, ComponentType<SceneProps>> = {
  sketch: SketchScene,
  cad: CadScene,
  eda: EdaScene,
  firmware: FirmwareScene,
  simulation: SimulationScene,
  manufacturing: ManufacturingScene,
  cross_domain: CrossDomainScene,
};

export function sceneFor(domain: OperationDomain): ComponentType<SceneProps> {
  return SCENES[sceneDomainOf(domain)];
}
