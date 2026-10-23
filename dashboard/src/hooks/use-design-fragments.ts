import { useQuery } from '@tanstack/react-query';

import { getDesignFragments } from '../api/endpoints/design-flows';

export const designFragmentKeys = {
  all: ['design-fragments'] as const,
};

/**
 * The domain fragment registry from the gateway (composable flows).
 *
 * Fragments are versioned files that change with a release, not per
 * interaction, so the cache is as long as the flow catalogue's.
 */
export function useDesignFragments() {
  return useQuery({
    queryKey: designFragmentKeys.all,
    queryFn: getDesignFragments,
    staleTime: 60_000,
  });
}
