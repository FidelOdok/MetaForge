import { describe, it, expect, vi, afterEach } from 'vitest';
import { registerProjectSearch } from '../workspace-tools';
import type { Project } from '../../types/project';
afterEach(()=>{delete (document as Document & {modelContext?: unknown}).modelContext;});
describe('workspace tools',()=>{
  it('filters the UI and rejects invalid input without changing it',()=>{
    const registerTool=vi.fn();Object.defineProperty(document,'modelContext',{value:{registerTool},configurable:true});
    const apply=vi.fn();const cleanup=registerProjectSearch([{id:'1',name:'Rover',description:'Inspection',status:'active'} as Project],apply);
    const [tool,options]=registerTool.mock.calls[0]!;
    expect(tool.name).toBe('filter_projects');expect(tool.inputSchema.required).toEqual(['query']);
    expect(tool.execute({query:'inspection',status:'active'})).toEqual({projects:[{id:'1',name:'Rover',status:'active'}]});
    expect(apply).toHaveBeenCalledWith('inspection','active');
    expect(()=>tool.execute({query:5})).toThrow();expect(apply).toHaveBeenCalledTimes(1);
    cleanup?.();expect(options.signal.aborted).toBe(true);
  });
  it('never substitutes empty projects for unavailable gateway data',()=>{
    const registerTool=vi.fn();Object.defineProperty(document,'modelContext',{value:{registerTool},configurable:true});
    const apply=vi.fn();registerProjectSearch(undefined,apply);
    expect(()=>registerTool.mock.calls[0]![0].execute({query:''})).toThrow('Project data is not available');expect(apply).not.toHaveBeenCalled();
  });
});
