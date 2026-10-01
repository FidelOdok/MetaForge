import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import axios, { type AxiosInstance } from 'axios';
import {
  installSampleAdapter,
  isSampleMode,
  sampleFileUrl,
  setSampleModeForTests,
  SAMPLE_PROJECT_ID,
} from '../sample-workspace';

describe('sample workspace', () => {
  let client: AxiosInstance;

  beforeEach(() => {
    setSampleModeForTests(true);
    client = axios.create({ baseURL: 'http://gateway.invalid/v1' });
    installSampleAdapter(client);
  });

  afterEach(() => setSampleModeForTests(false));

  it('serves the Drone FC nodes, relationships and project offline', async () => {
    expect(isSampleMode()).toBe(true);
    const nodes = await client.get('/twin/nodes', { params: { project_id: SAMPLE_PROJECT_ID } });
    // 14 since FORGE-305 added the two simulation_result nodes the sample
    // /simulation/results handler has been serving since FORGE-279.
    expect(nodes.data.total).toBe(14);
    expect(nodes.data.nodes.map((n: { id: string }) => n.id)).toContain('sample-pcb');

    const rels = await client.get('/twin/relationships');
    expect(rels.data.relationships).toHaveLength(13);

    const project = await client.get(`/projects/${SAMPLE_PROJECT_ID}`);
    expect(project.data.name).toBe('Drone FC · sample');
  });

  it('gives the FEA results and the twin nodes the same ids (FORGE-305)', async () => {
    // Two views of one analysis: the Sim page lists /simulation/results, the
    // Twin Viewer's inspector reads the node. Different ids would make the
    // sample workspace show the same FEA run as two unrelated things.
    const results = await client.get('/simulation/results');
    const resultIds = results.data.results.map((r: { id: string }) => r.id).sort();

    const nodes = await client.get('/twin/nodes', { params: { project_id: SAMPLE_PROJECT_ID } });
    const feaNodeIds = nodes.data.nodes
      .filter((n: { properties: Record<string, unknown> }) => n.properties.wp_type === 'simulation_result')
      .map((n: { id: string }) => n.id)
      .sort();

    expect(feaNodeIds).toEqual(resultIds);
    expect(feaNodeIds).toHaveLength(2);
  });

  it('carries mesh stats on the FEA nodes, which `properties` cannot (FORGE-305)', async () => {
    const nodes = await client.get('/twin/nodes', { params: { project_id: SAMPLE_PROJECT_ID } });
    const rev1 = nodes.data.nodes.find((n: { id: string }) => n.id === 'sample-fea-rev1');
    expect(rev1.meshStats).toEqual({ num_nodes: 12500, num_elements: 48000 });
    expect(rev1.properties.mesh_stats).toBeUndefined();
  });

  it('returns node detail, revisions and the sample GLB model', async () => {
    const node = await client.get('/twin/nodes/sample-pcb');
    expect(node.data.name).toBe('flight-controller.pcb');

    const versions = await client.get('/twin/nodes/sample-pcb/versions');
    expect(versions.data.revisions).toHaveLength(2);

    const model = await client.get('/twin/nodes/sample-enclosure/model?quality=standard');
    expect(model.data.glb_url).toBe('/samples/flight-controller.glb');
    expect(model.data.metadata.parts.length).toBeGreaterThan(0);
  });

  it('serves the drone URDF as the robot description file', async () => {
    const file = await client.get('/twin/nodes/sample-drone/file', { responseType: 'text' });
    expect(String(file.data)).toContain('<robot name="sample_drone">');
    expect(sampleFileUrl('sample-drone')).toBe('/samples/drone.urdf');
    expect(sampleFileUrl('sample-pcb')).toBe('/samples/sample-evidence.json');
  });

  it('answers a chat turn with the scripted sample assistant', async () => {
    const providers = await client.get('/harness/providers');
    expect(providers.data.active_model).toBe('Scripted sample assistant');

    const created = await client.post('/chat/threads', { scope_kind: 'project', scope_entity_id: SAMPLE_PROJECT_ID });
    await client.post(`/chat/threads/${created.data.id}/messages`, { content: 'Hello' });
    const thread = await client.get(`/chat/threads/${created.data.id}`);
    expect(thread.data.messages).toHaveLength(2);
    expect(thread.data.messages[1].actor_kind).toBe('agent');
    expect(thread.data.messages[1].content).toMatch(/Scripted sample response/);
  });

  it('applies an approved proposal to the in-memory twin, and resets', async () => {
    await client.post('/assistant/proposals/sample-proposal/decide', { decision: 'approve' });
    const firmware = await client.get('/twin/nodes/sample-firmware');
    expect(firmware.data.properties.telemetry_hz).toBe(1);
    const power = await client.get('/twin/nodes/sample-power');
    expect(power.data.status).toBe('stale');

    setSampleModeForTests(true); // reset, like a refresh
    const fresh = await client.get('/twin/nodes/sample-firmware');
    expect(fresh.data.properties.telemetry_hz).toBe(10);
  });

  it('rejects routes the sample workspace does not model', async () => {
    await expect(client.post('/twin/import', {})).rejects.toThrow(/unavailable in sample mode/);
  });

  it('leaves requests alone when sample mode is off', async () => {
    setSampleModeForTests(false);
    const req = client.get('/twin/nodes', { adapter: async (config) => ({ data: 'live', status: 200, statusText: 'OK', headers: {}, config }) });
    await expect(req).resolves.toMatchObject({ data: 'live' });
  });
});
