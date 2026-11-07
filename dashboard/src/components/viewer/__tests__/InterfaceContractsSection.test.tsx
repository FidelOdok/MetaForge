import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/interfaceContracts';
import type { ContractsResponse, InterfaceContractView } from '../../../api/endpoints/interfaceContracts';
import { InterfaceContractsSection } from '../InterfaceContractsSection';

const P = 'p1';

const CAD: InterfaceContractView = {
  key: 'IFC-CAD-EDA',
  ref: 'IFC-CAD-EDA@3',
  revision: 3,
  interface_version: 1,
  kind: 'cad_eda',
  title: 'Base enclosure and driver board',
  plain: 'The circuit board must fit inside the base',
  owning_domains: ['mechanical', 'electronics'],
  parameters: [
    { name: 'pcb_outline', label: 'PCB outline', value: { width: 60, height: 40 }, unit: 'mm', tolerance: 0.2, owner: 'electronics', basis: 'estimated', note: '' },
    { name: 'controller', label: 'Controller part', value: null, unit: '', tolerance: null, owner: 'electronics', basis: 'unknown', note: '' },
  ],
  links: [
    { role: 'enclosure', label: 'Base (CAD)', domain: 'mechanical', item_types: ['cad_model'], item_key: 'CAD-BASE', produced_by: '', linked: true, head_ref: 'CAD-BASE@2' },
    { role: 'pcb', label: 'PCB layout', domain: 'electronics', item_types: ['pcb_layout'], item_key: null, produced_by: 'kicad.generate_pcb', linked: false, head_ref: null },
  ],
  artifacts: [],
  rationale: '',
  agreements: [{ domain: 'mechanical', contract_revision: 3, interface_version: 1, decided_by: 'local:dashboard', decision_id: 'd1', at: '' }],
  awaiting_agreement: ['electronics'],
  status: 'violated',
  create_line: 'The circuit board must fit inside the base: does not fit yet',
  latest_check: {
    record_id: 'rec-1',
    staleness: 'current',
    stale: false,
    staleness_reason: null,
    validation: { status: 'FAIL', input_revisions: ['IFC-CAD-EDA@3', 'CAD-BASE@2'] },
    outcome: 'violated',
    summary: '2 checks: 1 violated, 1 not evaluated',
    evaluated_at: '',
    inputs: ['IFC-CAD-EDA@3', 'CAD-BASE@2'],
    checks: [
      {
        id: 'pcb_fits_envelope',
        label: 'PCB outline fits the enclosure',
        plain: 'The circuit board must fit inside the base',
        outcome: 'violated',
        message: 'The 60 x 40 mm board needs 62 x 42 mm; the envelope is 55 x 35 mm',
        values: { margin_width_mm: -7 },
        missing: [],
        inputs: ['PCB-MAIN@1', 'CAD-BASE@2'],
        parameters: ['pcb_outline'],
      },
      {
        id: 'led_position',
        label: 'LED position matches the contract',
        plain: '',
        outcome: 'not_evaluated',
        message: 'Not checked',
        values: {},
        missing: ['PCB layout: not linked yet (produced by kicad.generate_pcb)'],
        inputs: [],
        parameters: ['led_position'],
      },
    ],
  },
};

const SIM: InterfaceContractView = {
  ...CAD,
  key: 'IFC-SIMULATION',
  ref: 'IFC-SIMULATION@1',
  kind: 'simulation',
  title: 'Thermal analysis inputs',
  plain: 'The temperature check needs the lamp\'s shape, materials and LED heat',
  status: 'proposed',
  agreements: [],
  awaiting_agreement: ['mechanical', 'electronics', 'simulation'],
  latest_check: null,
};

const VIEW: ContractsResponse = { project_id: P, contracts: [CAD, SIM] };

describe('Engineering: Interfaces', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('lists contracts, coupled parameters, status and the evidence of the latest check', async () => {
    vi.spyOn(api, 'getContracts').mockResolvedValue(VIEW);
    render(<InterfaceContractsSection projectId={P} />);
    const block = await screen.findByTestId('contract-IFC-CAD-EDA');
    expect(within(block).getByTestId('contract-status-IFC-CAD-EDA')).toHaveTextContent('violated');
    expect(block).toHaveTextContent('interface v1');
    expect(block).toHaveTextContent('width 60, height 40 mm ± 0.2 mm');
    expect(block).toHaveTextContent('unknown');
    expect(block).toHaveTextContent('enclosure: CAD-BASE@2');
    expect(block).toHaveTextContent('pcb: not linked (produced by kicad.generate_pcb)');
    expect(block).toHaveTextContent('mechanical (local:dashboard)');
    expect(block).toHaveTextContent('check rec-1 · FAIL · current');
    const fit = within(block).getByTestId('contract-check-pcb_fits_envelope');
    expect(fit).toHaveTextContent('Violated');
    expect(fit).toHaveTextContent('margin_width_mm -7');
    expect(fit).toHaveTextContent('Read: PCB-MAIN@1, CAD-BASE@2');
    expect(within(block).getByTestId('contract-check-led_position')).toHaveTextContent(
      'Missing: PCB layout: not linked yet (produced by kicad.generate_pcb)',
    );
    expect(screen.getByTestId('contract-IFC-SIMULATION')).toHaveTextContent('Not checked yet.');
  });

  it('agrees for an owning domain and checks on request', async () => {
    vi.spyOn(api, 'getContracts').mockResolvedValue(VIEW);
    const agree = vi.spyOn(api, 'agreeContract').mockResolvedValue({ decision_id: 'd2', created: true, contract: 'IFC-CAD-EDA@3' });
    const check = vi.spyOn(api, 'checkContracts').mockResolvedValue({ checks: [] });
    const user = userEvent.setup();
    render(<InterfaceContractsSection projectId={P} />);
    const block = await screen.findByTestId('contract-IFC-CAD-EDA');
    await user.click(within(block).getByRole('button', { name: 'Agree for electronics' }));
    expect(agree).toHaveBeenCalledWith(P, 'IFC-CAD-EDA', 'electronics');
    await user.click(screen.getByRole('button', { name: 'Check now' }));
    expect(check).toHaveBeenCalledWith(P);
  });
});
