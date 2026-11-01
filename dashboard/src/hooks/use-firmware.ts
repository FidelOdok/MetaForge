import { useMutation } from '@tanstack/react-query';
import { createFirmwareScaffold } from '../api/endpoints/firmware';

/** FORGE-276: derive and record a firmware scaffold (pinmap + C header)
 * from the work product's real committed assembly.joints -- a pure append
 * (calling twice creates a second scaffold pair, mirroring bring-up
 * checklist/release-package's own create-a-new-snapshot-each-call
 * semantics), so there is no list query to invalidate -- each call's
 * result is shown directly, the same as the bring-up checklist panel
 * shows its own `create.data` before any list query would refetch. */
export function useCreateFirmwareScaffold(workProductId?: string, projectId?: string) {
  return useMutation({
    mutationFn: () => {
      if (!workProductId) throw new Error('useCreateFirmwareScaffold: no work product');
      return createFirmwareScaffold({ workProductId, projectId });
    },
  });
}
