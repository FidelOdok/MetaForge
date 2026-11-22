import { describe, expect, it } from 'vitest';
import { render, screen, within } from '../../../test/test-utils';
import type { PartDataCheck, RatingRow } from '../../../api/endpoints/library';
import { LifecycleBadge, PartDataChecklist, RatingsTables } from '../PartData';

const DS = 'https://www.ti.com/lit/ds/symlink/lm1117.pdf';

const RATINGS: RatingRow[] = [
  {
    param: 'input_voltage_v',
    kind: 'abs_max',
    min: null,
    typ: null,
    max: 20,
    unit: 'V',
    conditions: '',
    source_kind: 'datasheet',
    source_ref: `${DS} table 6.1`,
    needs_review: false,
  },
  {
    param: 'junction_temp_c',
    kind: 'recommended',
    min: 0,
    typ: null,
    max: 125,
    unit: 'degC',
    conditions: 'IOUT = 10 mA',
    source_kind: 'assumption',
    source_ref: '',
    needs_review: true,
  },
];

describe('Real part data (FORGE-906)', () => {
  it('lays out each datasheet table with its source, and marks an assumed row', () => {
    render(<RatingsTables ratings={RATINGS} />);
    const abs = screen.getByTestId('part-ratings-abs_max');
    expect(within(abs).getByText('Absolute maximum ratings')).toBeInTheDocument();
    const row = within(abs).getAllByRole('row')[1]!;
    expect(within(row).getAllByRole('cell').map((c) => c.textContent)).toEqual([
      'input voltage',
      '',
      '',
      '20',
      'V',
      '',
      'datasheet table 6.1',
    ]);
    expect(within(row).getByRole('link', { name: 'datasheet' })).toHaveAttribute('href', DS);
    const rec = screen.getByTestId('part-ratings-recommended');
    expect(rec).toHaveTextContent('°C');
    expect(rec).toHaveTextContent('assumption · review');
    expect(screen.queryByTestId('part-ratings-electrical')).toBeNull();
  });

  it('says what a part still lacks, item by item, and that SPICE is optional', () => {
    const check: PartDataCheck = {
      applies: true,
      complete: false,
      summary: '1 of 2 to do: KiCad footprint',
      items: [
        { key: 'mpn', label: 'Exact part number', status: 'ok', detail: 'LM1117MPX-3.3/NOPB', required: true },
        { key: 'ecad_footprint', label: 'KiCad footprint', status: 'missing', detail: 'not recorded', required: true },
        { key: 'spice', label: 'SPICE model', status: 'missing', detail: 'none attached', required: false },
      ],
    };
    render(<PartDataChecklist check={check} />);
    expect(screen.getByText('Not ready: 1 of 2 to do: KiCad footprint.')).toBeInTheDocument();
    expect(screen.getByTestId('part-data-ecad_footprint')).toHaveAttribute('data-status', 'missing');
    expect(screen.getByTestId('part-data-spice')).toHaveTextContent('(optional)');
  });

  it('shows an ended part plainly and says where its lifecycle came from', () => {
    render(
      <LifecycleBadge
        lifecycle={{ status: 'eol', source_kind: 'supplier', source_ref: 'Mouser', read_on: '2026-11-22', needs_review: false }}
      />,
    );
    const badge = screen.getByTestId('part-lifecycle');
    expect(badge).toHaveTextContent('End of life');
    expect(badge).toHaveAttribute('title', 'Mouser, read 2026-11-22');
  });
});
