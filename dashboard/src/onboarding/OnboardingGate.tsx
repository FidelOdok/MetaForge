import { useHealth } from '../hooks/use-health';
import { isGatewayUserConfigured } from '../lib/gatewayConfig';
import { GatewaySetup } from './GatewaySetup';

/**
 * Shows first-run setup to someone who has never had a working gateway.
 *
 * The trigger is deliberately narrow. "No gateway configured" is the obvious
 * test and it is wrong: an empty base means *same-origin relative paths*,
 * which is exactly how the Docker image and `npm run dev` are meant to work,
 * via the nginx and Vite proxies. Gating on that would put a setup wizard in
 * front of every local user for whom everything already works.
 *
 * So the condition is behavioural rather than declarative — the gateway is
 * unreachable AND the user has never chosen one:
 *
 *   health loading   -> app. Do not flash a wizard during the first poll.
 *   health ok        -> app. Something is answering; nothing to set up.
 *   error, chosen    -> app. They configured it and it is down or wrong;
 *                       lock them out and they cannot reach Settings to fix
 *                       it. A visible failure belongs in the app, not here.
 *   error, unchosen  -> setup. Nothing has ever worked and they have never
 *                       been asked. This is the app.metaforge.uk visitor.
 */
export function OnboardingGate({ children }: { children: React.ReactNode }) {
  const { isError, isLoading } = useHealth();

  const neverWorked = isError && !isLoading && !isGatewayUserConfigured();
  if (neverWorked) return <GatewaySetup />;

  return <>{children}</>;
}
