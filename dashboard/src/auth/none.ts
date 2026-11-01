import type { AuthClient } from './types';

/**
 * The default `metaforge:auth` target: no authentication implementation.
 *
 * This is not a degraded state — it is what every local, single-user MetaForge
 * install looks like, and it is what this repository ships. The gateway's
 * matching default is `METAFORGE_AUTH_MODE=off`, which rejects nothing, so a
 * dashboard with no client has nothing to do.
 *
 * A build that *does* have a provider points `METAFORGE_AUTH_MODULE` at a
 * module exporting one in this module's place; see `src/auth/types.ts`.
 */
export const authClient: AuthClient | null = null;
