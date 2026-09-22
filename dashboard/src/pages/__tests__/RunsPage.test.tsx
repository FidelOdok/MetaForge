import { describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';
vi.mock('../../hooks/use-runs',()=>({useRuns:vi.fn()}));
import { useRuns } from '../../hooks/use-runs';
import { RunsPage } from '../RunsPage';
describe('Runs workspace',()=>{
  it('does not report zero runs when the gateway fails',()=>{
    vi.mocked(useRuns).mockReturnValue({isError:true,isLoading:false} as ReturnType<typeof useRuns>);
    render(<RunsPage/>);expect(screen.getByRole('alert')).toHaveTextContent('Runs could not be loaded');expect(screen.queryByText('No runs yet')).not.toBeInTheDocument();
  });
  it('filters real run statuses and preserves the inspect link',async()=>{
    vi.mocked(useRuns).mockReturnValue({data:[{id:'r1',status:'awaiting_approval',request:{goal:'Validate torque'},updatedAt:1700000000}],isLoading:false,refetch:vi.fn()} as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage/>);const user=userEvent.setup();expect(screen.getByRole('link',{name:'Inspect'})).toHaveAttribute('href','/runs/r1');
    await user.selectOptions(screen.getByLabelText('Run status'),'completed');expect(screen.getByText('No matching runs')).toBeInTheDocument();
  });
});
