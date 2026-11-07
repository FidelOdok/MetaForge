import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import type { InterfaceContractView } from '../../../api/endpoints/interfaceContracts';
import type { DfmView } from '../../../api/endpoints/dfmFindings';
import { useComponentSelection } from '../../../store/component-selection-store';

const getContracts = vi.fn();
const checkContracts = vi.fn();
vi.mock('../../../api/endpoints/interfaceContracts', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/interfaceContracts')>()),
  getContracts: (...a: unknown[]) => getContracts(...a),
  checkContracts: (...a: unknown[]) => checkContracts(...a),
}));
const getDfm = vi.fn();
const evaluateDfm = vi.fn();
vi.mock('../../../api/endpoints/dfmFindings', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/dfmFindings')>()),
  getDfm: (...a: unknown[]) => getDfm(...a),
  evaluateDfm: (...a: unknown[]) => evaluateDfm(...a),
}));

import { CheckStage } from '../CheckStage';

const P = 'proj-check';

const CONTRACT = {
  key: 'IFC-CAD-EDA',
  plain: 'The circuit board must fit inside the base',
  status: 'violated',
  latest_check: { stale: false, outcome: 'violated' },
} as unknown as InterfaceContractView;
const UNCHECKED = { key: 'IFC-SIM', plain: 'The temperature check needs the heat load', status: 'proposed', latest_check: null } as unknown as InterfaceContractView;

const DFM = {
  project_id: P,
  status: 'preliminary_guidance',
  status_label: 'Preliminary guidance',
  disclaimer: 'Preliminary guidance from rules, not a guarantee.',
  components: [
    {
      component_id: 'CMP-base',
      name: 'Base',
      findings: [{ id: 'f1', message: 'Wall thinner than 1.2 mm', severity: 'blocking', decision: null }],
    },
  ],
} as unknown as DfmView;

beforeEach(() => {
  getContracts.mockReset().mockResolvedValue({ project_id: P, contracts: [CONTRACT, UNCHECKED] });
  checkContracts.mockReset().mockResolvedValue({ checks: [] });
  getDfm.mockReset().mockResolvedValue(DFM);
  evaluateDfm.mockReset().mockResolvedValue(DFM);
  useComponentSelection.setState({ selected: {} });
});

describe('Check', () => {
  it('says in plain words where each contract stands', async () => {
    render(<CheckStage projectId={P} onAsk={vi.fn()} />);
    expect(await screen.findByTestId('fit-IFC-CAD-EDA')).toHaveTextContent('The circuit board must fit inside the base: does not fit yet');
    expect(screen.getByTestId('fit-IFC-SIM')).toHaveTextContent('not checked yet');
    expect(screen.getByTestId('check-contracts')).not.toHaveTextContent('IFC-');
  });

  it('lists the open DFM results, and selecting one highlights the part', async () => {
    render(<CheckStage projectId={P} onAsk={vi.fn()} />);
    expect(await screen.findByText(/Wall thinner than 1.2 mm/)).toBeInTheDocument();
    expect(screen.getByTestId('check-dfm')).toHaveTextContent('(blocks making)');
    await userEvent.click(screen.getByRole('button', { name: 'Base' }));
    expect(useComponentSelection.getState().selected[P]).toMatchObject({ componentId: 'CMP-base' });
  });

  it('has one primary action, which runs the checks', async () => {
    render(<CheckStage projectId={P} onAsk={vi.fn()} />);
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    await userEvent.click(screen.getByTestId('stage-primary'));
    await waitFor(() => expect(checkContracts).toHaveBeenCalledWith(P));
    expect(evaluateDfm).toHaveBeenCalledWith(P);
  });
});
