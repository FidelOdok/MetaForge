import { useMutation } from '@tanstack/react-query';
import { getBomRisk } from '../api/endpoints/bom-risk';

/** FORGE-268: on-demand supply-chain risk scoring for a project's BOM --
 * a mutation (not an auto-fetching query), since each call resolves real
 * distributor offers for every real BOM line, the same "explicit trigger"
 * pattern FORGE-273's useOverhangCheck established. */
export function useBomRisk() {
  return useMutation({ mutationFn: getBomRisk });
}
