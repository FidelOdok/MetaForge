import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '../../test/test-utils';

import { FragmentsPage } from '../FragmentsPage';
import { useDesignFragments } from '../../hooks/use-design-fragments';

vi.mock('../../hooks/use-design-fragments', () => ({ useDesignFragments: vi.fn() }));

const thermal = {
  id: 'thermal_v1',
  version: '1.0.0',
  domain: 'thermal',
  label: 'Thermal system',
  description: 'Heat load, cooling architecture, heat-transfer analysis and temperature verification.',
  spine: 'spine_v1',
  requires: [],
  keywords: ['thermal'],
  tasks: [
    {
      id: 'thermal.heat_load',
      title: 'Heat-load estimation',
      stage: 'design',
      inputs: ['design_value:power_dissipation_w'],
      outputs: ['design_value:heat_load_w'],
      checks: ['heat_load_w >= 0 W'],
      capabilities: ['twin.record_engineering_entity'],
    },
    {
      id: 'thermal.heat_transfer',
      title: 'Heat-transfer analysis',
      stage: 'simulation',
      inputs: ['design_value:heat_load_w'],
      outputs: ['design_value:max_junction_temp_c'],
      checks: ['max_junction_temp_c <= max_allowed_junction_temp_c'],
      capabilities: ['calculix.run_thermal'],
    },
  ],
  blocks: [
    {
      id: 'energy_closure',
      stage: 'design',
      members: ['battery', 'mass', 'power'],
      solvesFor: ['battery_capacity_wh', 'total_mass_kg'],
      tolerance: 0.01,
      maxIterations: 20,
    },
  ],
};

function mockQuery(state: Record<string, unknown>) {
  vi.mocked(useDesignFragments).mockReturnValue(state as unknown as ReturnType<typeof useDesignFragments>);
}

describe('FragmentsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists each fragment with its tasks grouped by the gate that reviews them', () => {
    mockQuery({ data: { fragments: [thermal] }, isSuccess: true, isLoading: false, isError: false });
    render(<FragmentsPage />);
    expect(screen.getByRole('heading', { name: /Thermal system/ })).toBeInTheDocument();
    expect(screen.getByText('Reviewed at the design gate')).toBeInTheDocument();
    expect(screen.getByText('Reviewed at the simulation gate')).toBeInTheDocument();
    expect(screen.getByText('max_junction_temp_c <= max_allowed_junction_temp_c')).toBeInTheDocument();
    expect(screen.getByText('calculix.run_thermal')).toBeInTheDocument();
  });

  it('describes a coupled loop and its convergence rule', () => {
    mockQuery({ data: { fragments: [thermal] }, isSuccess: true, isLoading: false, isError: false });
    render(<FragmentsPage />);
    expect(screen.getByText(/change by less than/)).toHaveTextContent('1.0% (at most 20 passes)');
  });

  it('says so when the registry cannot be loaded', () => {
    mockQuery({ data: undefined, isSuccess: false, isLoading: false, isError: true });
    render(<FragmentsPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load the fragment registry');
  });

  it('says so when there are none', () => {
    mockQuery({ data: { fragments: [] }, isSuccess: true, isLoading: false, isError: false });
    render(<FragmentsPage />);
    expect(screen.getByText('The gateway serves no domain fragments.')).toBeInTheDocument();
  });
});
