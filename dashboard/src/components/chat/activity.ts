// Turns the chat stream's raw events (agent.step, agent.action_started,
// agent.thinking, …) into a few plain-language steps a person can follow:
// "Read the design record · done", "Waiting for your approval to change the
// design". Thinking deltas and typing pings are dropped; a started action and
// the step that finishes it become one line.

export interface StreamActivity {
  kind: string;
  data: unknown;
  at: string;
}

export type StepState = 'running' | 'done' | 'failed' | 'waiting' | 'info';

export interface ActivityStep {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
  at: string;
}

// Tool id prefix -> what it does, in the person's words. Longest prefix wins.
const TOOL_LABELS: [prefix: string, label: string][] = [
  ['twin.propose_change', 'Proposed a design change'],
  ['twin.commit_geometry', 'Saved the geometry to the design'],
  ['twin.record_decision', 'Recorded a decision'],
  ['twin.get', 'Read the design record'],
  ['twin.find', 'Searched the design'],
  ['twin.list', 'Read the design record'],
  ['twin.', 'Worked with the design record'],
  ['chat.render_ui', 'Prepared a visual answer'],
  ['chat.set_project_scope', 'Switched project'],
  ['search_tools', 'Looked for the right tool'],
  ['read_tool_result', 'Read a full tool result'],
  ['cadquery.', 'Built geometry'],
  ['freecad.measure', 'Measured the model'],
  ['freecad.', 'Worked on the CAD model'],
  ['calculix.run_thermal', 'Ran a thermal check'],
  ['calculix.', 'Ran a stress check'],
  ['kicad.run_drc', 'Checked the board layout'],
  ['kicad.run_erc', 'Checked the schematic'],
  ['kicad.', 'Worked on the circuit board'],
  ['spice.', 'Simulated the circuit'],
  ['knowledge.', 'Searched the knowledge base'],
  ['web.', 'Searched the web'],
  ['run.start_design_flow', 'Started a design run'],
  ['run.', 'Checked a design run'],
  ['session.', 'Updated the session record'],
  ['generate_cad', 'Generated a CAD design'],
  ['check_', 'Ran a design check'],
];

/** `mcp_twin_get_node` (native tool name) and `twin.get_node` both read as `twin.get_node`. */
export function toolId(name: string): string {
  if (!name.startsWith('mcp_')) return name;
  const rest = name.slice(4);
  const cut = rest.indexOf('_');
  return cut < 0 ? rest : `${rest.slice(0, cut)}.${rest.slice(cut + 1)}`;
}

export function toolLabel(name: string): string {
  const id = toolId(name);
  let best: [string, string] | null = null;
  for (const entry of TOOL_LABELS) {
    if (id.startsWith(entry[0]) && (!best || entry[0].length > best[0].length)) best = entry;
  }
  if (best) return best[1];
  // Unknown tool: "freecad.measure_distance" -> "Freecad: measure distance".
  const [family, action] = id.includes('.') ? id.split(/\.(.+)/) : ['', id];
  const words = (action ?? id).replace(/[_-]+/g, ' ').trim();
  return family ? `${family[0]!.toUpperCase()}${family.slice(1)}: ${words}` : words;
}

function shortError(text: unknown): string {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  return s.length > 140 ? `${s.slice(0, 137)}…` : s;
}

function obj(data: unknown): Record<string, unknown> {
  return typeof data === 'object' && data !== null ? (data as Record<string, unknown>) : {};
}

export function summariseActivity(events: StreamActivity[]): ActivityStep[] {
  const steps: ActivityStep[] = [];
  // The most recent running step per tool, so its finishing step updates it in place.
  const running = new Map<string, number>();
  events.forEach((evt, n) => {
    const data = obj(evt.data);
    switch (evt.kind) {
      case 'agent.action_started': {
        const tool = String(data.tool ?? '');
        if (!tool) return;
        running.set(toolId(tool), steps.length);
        steps.push({ key: `a${n}`, label: toolLabel(tool), state: 'running', at: evt.at });
        return;
      }
      case 'agent.step': {
        const step = obj(data.step);
        const tool = typeof step.tool === 'string' ? step.tool : '';
        if (!tool || step.final) return;
        const failed = Boolean(step.error);
        const state: StepState = failed ? 'failed' : 'done';
        const detail = failed ? shortError(step.error) : undefined;
        const at = running.get(toolId(tool));
        if (at !== undefined && steps[at]?.state === 'running') {
          steps[at] = { ...steps[at]!, state, detail };
          running.delete(toolId(tool));
        } else {
          steps.push({ key: `s${n}`, label: toolLabel(tool), state, detail, at: evt.at });
        }
        return;
      }
      case 'tool.approval_requested': {
        const tool = String(data.tool ?? '');
        steps.push({
          key: `w${n}`,
          label: `Waiting for your approval: ${toolLabel(tool).toLowerCase()}`,
          state: 'waiting',
          at: evt.at,
        });
        return;
      }
      case 'scope.changed': {
        const label =
          data.scope_kind === 'project'
            ? `Now working in ${String(data.project_name ?? 'the project')}`
            : 'Left the project';
        steps.push({ key: `p${n}`, label, state: 'info', at: evt.at });
        return;
      }
      case 'error':
        steps.push({ key: `e${n}`, label: 'Something went wrong', state: 'failed', detail: shortError(data.error), at: evt.at });
        return;
      case 'agent.done':
        // A step still "running" when the turn ends did not report back.
        for (const i of running.values()) {
          const s = steps[i];
          if (s && s.state === 'running') steps[i] = { ...s, state: 'failed', detail: 'No result was reported' };
        }
        running.clear();
        return;
      default:
        // agent.typing, agent.thinking deltas, message.* and context.stats are not steps.
        return;
    }
  });
  return steps;
}
