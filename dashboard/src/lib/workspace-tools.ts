import type { Project } from '../types/project';

type Tool = { name: string; description: string; inputSchema: object; annotations: { readOnlyHint: boolean; untrustedContentHint: boolean }; execute: (input: unknown) => unknown };
type ModelContext = { registerTool: (tool: Tool, options: { signal: AbortSignal }) => void | Promise<void> };

/** Optional browser-agent access to the same local search controls; no API mutations. */
export function registerProjectSearch(projects: Project[] | undefined, apply: (query:string, status:string)=>void) {
  const context = (document as Document & { modelContext?: ModelContext }).modelContext;
  if (!context?.registerTool) return;
  const lifecycle = new AbortController();
  try {
    void Promise.resolve(context.registerTool({
      name: 'filter_projects', description: 'Filter the visible project library by name, description, and status. Changes only the current view; never creates or modifies engineering data.',
      inputSchema: { type: 'object', properties: { query: { type: 'string' }, status: { type: 'string', enum: ['all','active','draft','archived'] } }, required: ['query'], additionalProperties: false },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      execute(input: unknown) {
        if (!input || typeof input !== 'object') throw new Error('Expected an object.');
        const args = input as Record<string,unknown>;
        const status = args.status ?? 'all';
        if (typeof args.query !== 'string' || typeof status !== 'string' || !['all','active','draft','archived'].includes(status) || Object.keys(args).some(k=>!['query','status'].includes(k))) throw new Error('Provide a text query and a supported project status.');
        if (!projects) throw new Error('Project data is not available. Connect the gateway and try again.');
        const query = args.query;
        apply(query, status);
        return { projects: projects.filter(p=>(status==='all'||p.status===status)&&`${p.name} ${p.description}`.toLowerCase().includes(query.toLowerCase())).map(p=>({id:p.id,name:p.name,status:p.status})) };
      },
    }, { signal: lifecycle.signal })).catch(()=>{});
  } catch { /* Optional capability: ordinary browser interactions remain available. */ }
  return () => lifecycle.abort();
}
