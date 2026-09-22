import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';
vi.mock('../../hooks/use-projects', () => ({ useProjects: vi.fn(), useCreateProject: vi.fn() }));
vi.mock('../../hooks/use-health', () => ({ useHealth: vi.fn(() => ({ data: undefined, isLoading: false })) }));
vi.mock('../../hooks/use-runs', () => ({ useRuns: vi.fn(() => ({ data: [], isLoading: false })) }));
import { ProjectsPage } from '../ProjectsPage';
import { useProjects, useCreateProject } from '../../hooks/use-projects';
const project = { id:'p1', name:'Inspection rover', description:'Mobile platform', status:'active', work_products:[], agentCount:2, lastUpdated:'2026-09-21T10:00:00Z', createdAt:'2026-09-21T10:00:00Z' };
const mockProjects = vi.mocked(useProjects);
const mutate = vi.fn();
beforeEach(()=>{
  vi.mocked(useCreateProject).mockReturnValue({ mutate, reset:vi.fn(), isPending:false } as unknown as ReturnType<typeof useCreateProject>);
  mockProjects.mockReturnValue({data:[],isLoading:false,isError:false} as unknown as ReturnType<typeof useProjects>);
  HTMLDialogElement.prototype.showModal = function(){this.setAttribute('open','');};
  HTMLDialogElement.prototype.close = function(){this.removeAttribute('open');};
});
describe('Projects workspace',()=>{
  it('distinguishes unavailable data from an empty workspace',()=>{
    mockProjects.mockReturnValue({data:undefined,isLoading:false,isError:true} as ReturnType<typeof useProjects>);
    render(<ProjectsPage/>);
    expect(screen.getByText('Connect your engineering gateway')).toBeInTheDocument();
    expect(screen.queryByText('Start with an engineering intent')).not.toBeInTheDocument();
    expect(screen.getByText('Project data unavailable')).toBeInTheDocument();
  });
  it('announces loading',()=>{
    mockProjects.mockReturnValue({data:undefined,isLoading:true} as ReturnType<typeof useProjects>);
    render(<ProjectsPage/>); expect(screen.getByRole('status')).toHaveTextContent('Loading your workspace');
  });
  it('shows a useful empty state',()=>{render(<ProjectsPage/>);expect(screen.getByText('Start with an engineering intent')).toBeInTheDocument();});
  it('filters projects and restores them with Clear filters',async()=>{
    mockProjects.mockReturnValue({data:[project],isLoading:false} as unknown as ReturnType<typeof useProjects>);
    render(<ProjectsPage/>);const user=userEvent.setup();
    expect(screen.getByRole('link',{name:/Inspection rover/})).toHaveAttribute('href','/projects/p1');
    await user.type(screen.getByRole('textbox',{name:'Search projects'}),'unmatched');
    expect(screen.getByText('No matching projects')).toBeInTheDocument();
    await user.click(screen.getByRole('button',{name:'Clear filters'}));
    expect(screen.getByRole('link',{name:/Inspection rover/})).toBeInTheDocument();
    await user.click(screen.getByRole('button',{name:'Draft'}));
    expect(screen.getByText('No matching projects')).toBeInTheDocument();
  });
  it('submits the gateway contract from a labeled dialog',async()=>{
    render(<ProjectsPage/>); const user=userEvent.setup();
    await user.click(screen.getByRole('button',{name:'New project'}));
    expect(screen.getByRole('dialog',{name:'Create a project'})).toBeInTheDocument();
    await user.type(screen.getByLabelText('Project name'),'  Rover  ');
    await user.type(screen.getByLabelText(/Description/),'Field inspection');
    await user.click(screen.getByRole('button',{name:'Create project'}));
    expect(mutate).toHaveBeenCalledWith({name:'Rover',description:'Field inspection'},expect.any(Object));
  });
});
