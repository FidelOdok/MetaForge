import { describe, expect, it } from 'vitest';
import { summariseActivity, toolId, toolLabel, type StreamActivity } from '../activity';

const at = '2026-11-04T12:00:00Z';
const ev = (kind: string, data: unknown): StreamActivity => ({ kind, data, at });

describe('tool labels', () => {
  it('reads native and MCP names the same way', () => {
    expect(toolId('mcp_twin_get_node')).toBe('twin.get_node');
    expect(toolLabel('mcp_twin_get_node')).toBe('Read the design record');
    expect(toolLabel('twin.propose_change')).toBe('Proposed a design change');
  });

  it('prefers the most specific label', () => {
    expect(toolLabel('calculix.run_thermal')).toBe('Ran a thermal check');
    expect(toolLabel('calculix.run_fea')).toBe('Ran a stress check');
  });

  it('turns an unknown tool id into words rather than showing the id', () => {
    expect(toolLabel('omniverse.export_usd_stage')).toBe('Omniverse: export usd stage');
  });
});

describe('summariseActivity', () => {
  it('collapses a started action and its finished step into one line', () => {
    const steps = summariseActivity([
      ev('agent.typing', { agent_id: 'a' }),
      ev('agent.thinking', { delta: 'let me', kind: 'draft' }),
      ev('agent.thinking', { delta: ' look', kind: 'draft' }),
      ev('agent.action_started', { tool: 'mcp_twin_get_node' }),
      ev('agent.step', { step: { tool: 'mcp_twin_get_node', observation: { id: 'n1' } } }),
      ev('agent.step', { step: { final: true, tool: null } }),
      ev('agent.done', {}),
    ]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ label: 'Read the design record', state: 'done' });
  });

  it('shows a failure with a short reason', () => {
    const steps = summariseActivity([
      ev('agent.action_started', { tool: 'calculix.run_fea' }),
      ev('agent.step', { step: { tool: 'calculix.run_fea', error: 'mesh failed: ' + 'x'.repeat(300) } }),
    ]);
    expect(steps[0]!.state).toBe('failed');
    expect(steps[0]!.detail!.length).toBeLessThanOrEqual(140);
  });

  it('says when it is waiting for the person', () => {
    const [step] = summariseActivity([ev('tool.approval_requested', { run_id: 'r', tool: 'twin.commit_geometry' })]);
    expect(step).toMatchObject({ state: 'waiting', label: 'Waiting for your approval: saved the geometry to the design' });
  });

  it('marks a step that never reported back when the turn ends', () => {
    const steps = summariseActivity([ev('agent.action_started', { tool: 'web.search' }), ev('agent.done', {})]);
    expect(steps[0]).toMatchObject({ state: 'failed', detail: 'No result was reported' });
  });

  it('reports scope changes and errors in plain words', () => {
    const steps = summariseActivity([
      ev('scope.changed', { scope_kind: 'project', scope_entity_id: 'p1', project_name: 'Desk hook' }),
      ev('scope.changed', { scope_kind: 'assistant', scope_entity_id: 't1', project_name: null }),
      ev('error', { error: 'provider unavailable' }),
    ]);
    expect(steps.map((s) => s.label)).toEqual(['Now working in Desk hook', 'Left the project', 'Something went wrong']);
    expect(steps[2]!.detail).toBe('provider unavailable');
  });
});
