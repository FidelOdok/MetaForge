import { useState, useMemo } from 'react';
import { EmptyState } from '../components/ui/EmptyState';
import { StatusBadge } from '../components/shared/StatusBadge';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { useBom, useHierarchicalBom } from '../hooks/use-bom';
import { useBomRisk } from '../hooks/use-bom-risk';
import { useActiveProject } from '../hooks/use-active-project';
import { useSelectComponent } from '../hooks/use-component-selection';
import type { BomComponent, HierarchicalBomLine } from '../types/bom';
import type {
  Candidate,
  MarginOp,
  SelectComponentResult,
} from '../types/component-selection';

type BomView = 'flat' | 'hierarchical';

const FIELD_STYLE: React.CSSProperties = {
  background: 'var(--mf-c-191b22)',
  border: '1px solid var(--mf-r-65-72-90-0p3)',
};

type SortField = 'designator' | 'partNumber' | 'description' | 'manufacturer' | 'quantity' | 'unitPrice' | 'status';
type SortDir = 'asc' | 'desc';

// Common ISO 4217 symbols. Falls back to "<code> " prefix for anything else
// (e.g. "SEK 42.00") rather than silently mislabeling a non-USD price as $.
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  GBP: '£',
  EUR: '€',
  JPY: '¥',
};

function formatPrice(amount: number, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency];
  return symbol ? `${symbol}${amount.toFixed(2)}` : `${currency} ${amount.toFixed(2)}`;
}

function BomRow({ component }: { component: BomComponent }) {
  return (
    <tr
      className="hover:bg-[var(--mf-c-282a30)] cursor-default"
      style={{ height: '36px', borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}
    >
      <td className="px-3">
        {component.imageUrl ? (
          <img
            src={component.imageUrl}
            alt={component.partNumber}
            className="rounded"
            style={{ width: '20px', height: '20px', objectFit: 'contain', background: 'var(--mf-c-191b22)' }}
          />
        ) : (
          <div style={{ width: '20px', height: '20px' }} />
        )}
      </td>
      <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
        {component.designator}
      </td>
      <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
        {component.purchaseUrl ? (
          <a
            href={component.purchaseUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
            title="Open product page"
          >
            {component.partNumber}
          </a>
        ) : (
          component.partNumber
        )}
        {component.datasheetUrl && (
          <a
            href={component.datasheetUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="Open datasheet"
            className="material-symbols-outlined align-middle ml-1 text-on-surface-variant hover:text-on-surface"
            style={{ fontSize: '13px' }}
          >
            description
          </a>
        )}
      </td>
      <td className="px-3 text-xs text-on-surface-variant">
        {component.description}
      </td>
      <td className="px-3 text-xs text-on-surface-variant whitespace-nowrap">
        {component.manufacturer}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface">
        {component.quantity}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface">
        {formatPrice(component.unitPrice, component.priceCurrency)}
      </td>
      <td className="px-3">
        <StatusBadge status={component.status} />
      </td>
    </tr>
  );
}

/** FORGE-267: one derived EBOM row, indented by its depth in the product
 * hierarchy (path.length) -- deliberately no where-used drawer or 3D
 * click-to-highlight in this first pass (see FORGE-267's own scope note). */
function HierarchicalBomRow({ line }: { line: HierarchicalBomLine }) {
  const depth = line.path.length - 1;
  const leafName = line.path[line.path.length - 1];
  return (
    <tr
      className="hover:bg-[var(--mf-c-282a30)] cursor-default"
      style={{ height: '36px', borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}
    >
      <td
        className="px-3 text-xs text-on-surface whitespace-nowrap"
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        title={line.path.join(' / ')}
      >
        {leafName}
      </td>
      <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
        {line.partNumber ?? '—'}
      </td>
      <td className="px-3 text-xs text-on-surface-variant">{line.description}</td>
      <td className="px-3 text-xs text-on-surface-variant whitespace-nowrap">
        {line.manufacturer ?? '—'}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface">{line.quantity}</td>
      <td className="px-3 text-right font-mono text-xs text-on-surface">
        {line.unitCost != null ? `$${line.unitCost.toFixed(2)}` : '—'}
      </td>
      <td className="px-3 text-xs text-on-surface-variant capitalize">
        {line.source === 'instance_of' ? 'COTS' : 'Fabricated'}
      </td>
    </tr>
  );
}

interface RequiredSpecRow {
  name: string;
  op: MarginOp;
  value: string;
}

interface CandidateRow {
  mpn: string;
  manufacturer: string;
  specs: Record<string, string>;
}

const EMPTY_REQUIRED_SPEC: RequiredSpecRow = { name: '', op: '>=', value: '' };
const EMPTY_CANDIDATE: CandidateRow = { mpn: '', manufacturer: '', specs: {} };

/** FORGE-265 (gap G-C1): requirement-driven component selection. Unlike
 * TradeStudySection (RequirementsPage.tsx), there is no list to fetch first
 * -- a candidate is just an mpn + caller-asserted datasheet specs typed in
 * here at comparison time, so this form covers "define what's required",
 * "list the real candidates", and "select one" in a single submit
 * (POST /v1/component-selection/select). Spec values are real published
 * datasheet numbers a human/agent types in, not scraped by any pipeline --
 * see docs/twin_schema.md section 2.23. */
function ComponentSelectionSection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const [category, setCategory] = useState('servo');
  const [purchaseUnit, setPurchaseUnit] = useState<'discrete_part' | 'cots_assembly'>(
    'cots_assembly',
  );
  const [title, setTitle] = useState('');
  const [rationale, setRationale] = useState('');
  const [requiredSpecs, setRequiredSpecs] = useState<RequiredSpecRow[]>([
    { ...EMPTY_REQUIRED_SPEC },
  ]);
  const [candidates, setCandidates] = useState<CandidateRow[]>([
    { ...EMPTY_CANDIDATE },
    { ...EMPTY_CANDIDATE },
  ]);
  const [selectedMpn, setSelectedMpn] = useState<string | null>(null);
  const [result, setResult] = useState<SelectComponentResult | null>(null);

  const select = useSelectComponent();
  const specNames = requiredSpecs.map((r) => r.name.trim()).filter(Boolean);

  const canSubmit =
    title.trim() !== '' &&
    rationale.trim() !== '' &&
    !!selectedMpn &&
    specNames.length > 0 &&
    candidates.filter((c) => c.mpn.trim() && c.manufacturer.trim()).length >= 2;

  const handleSubmit = () => {
    if (!canSubmit || !selectedMpn) return;
    const requiredSpecsPayload: Record<string, { op: MarginOp; value: number }> = {};
    for (const r of requiredSpecs) {
      const name = r.name.trim();
      if (!name || r.value === '') continue;
      requiredSpecsPayload[name] = { op: r.op, value: Number(r.value) };
    }
    const candidatesPayload: Candidate[] = candidates
      .filter((c) => c.mpn.trim() && c.manufacturer.trim())
      .map((c) => {
        const specs: Record<string, number> = {};
        for (const name of specNames) {
          const v = c.specs[name];
          if (v !== undefined && v !== '') specs[name] = Number(v);
        }
        return { mpn: c.mpn.trim(), manufacturer: c.manufacturer.trim(), specs };
      });

    select.mutate(
      {
        candidates: candidatesPayload,
        requiredSpecs: requiredSpecsPayload,
        selectedMpn,
        category: category.trim(),
        purchaseUnit,
        title: title.trim(),
        rationale: rationale.trim(),
        projectId,
      },
      {
        onSuccess: (data) => {
          toast.success('Component selected -- recorded as a Decision + BOM item');
          setResult(data);
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not record the selection');
        },
      },
    );
  };

  return (
    <div className="mt-6" data-testid="component-selection-section">
      <h2 className="mb-2 text-sm font-medium text-on-surface" style={{ margin: 0 }}>
        Requirement-driven component selection
      </h2>
      <p className="mb-2 text-xs text-on-surface-variant">
        Compare real candidate parts against required specs (caller-asserted datasheet values,
        e.g. a servo's published torque_kg_cm) and select one -- recorded as a real Decision +
        BOMItem, not just an assertion.
      </p>

      <div
        className="rounded-lg p-3"
        style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
      >
        <div className="mb-3 flex flex-wrap gap-2">
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Category
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="e.g. servo"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '140px' }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Purchase unit
            <select
              value={purchaseUnit}
              onChange={(e) => setPurchaseUnit(e.target.value as 'discrete_part' | 'cots_assembly')}
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '160px' }}
            >
              <option value="cots_assembly">cots_assembly</option>
              <option value="discrete_part">discrete_part</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant" style={{ flex: 1, minWidth: '200px' }}>
            Title
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Elbow joint actuator"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '100%' }}
            />
          </label>
        </div>

        <div className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
              Required specs
            </span>
            <button
              type="button"
              data-testid="add-required-spec-button"
              onClick={() => setRequiredSpecs((rs) => [...rs, { ...EMPTY_REQUIRED_SPEC }])}
              className="rounded px-2 py-0.5 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            >
              + Add spec
            </button>
          </div>
          <div className="flex flex-col gap-1">
            {requiredSpecs.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  value={r.name}
                  onChange={(e) =>
                    setRequiredSpecs((rs) =>
                      rs.map((row, j) => (j === i ? { ...row, name: e.target.value } : row)),
                    )
                  }
                  placeholder="e.g. torque_kg_cm"
                  className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                  style={{ ...FIELD_STYLE, width: '160px' }}
                />
                <select
                  value={r.op}
                  onChange={(e) =>
                    setRequiredSpecs((rs) =>
                      rs.map((row, j) => (j === i ? { ...row, op: e.target.value as MarginOp } : row)),
                    )
                  }
                  className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                  style={{ ...FIELD_STYLE, width: '64px' }}
                >
                  <option value=">=">&ge;</option>
                  <option value="<=">&le;</option>
                </select>
                <input
                  value={r.value}
                  onChange={(e) =>
                    setRequiredSpecs((rs) =>
                      rs.map((row, j) => (j === i ? { ...row, value: e.target.value } : row)),
                    )
                  }
                  type="number"
                  placeholder="required value"
                  className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                  style={{ ...FIELD_STYLE, width: '120px' }}
                />
              </div>
            ))}
          </div>
        </div>

        <div className="mb-3 overflow-x-auto">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
              Candidates
            </span>
            <button
              type="button"
              data-testid="add-candidate-button"
              onClick={() => setCandidates((cs) => [...cs, { ...EMPTY_CANDIDATE }])}
              className="rounded px-2 py-0.5 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            >
              + Add candidate
            </button>
          </div>
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant" />
                <th className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left">
                  MPN
                </th>
                <th className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left">
                  Manufacturer
                </th>
                {specNames.map((name) => (
                  <th
                    key={name}
                    className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right"
                  >
                    {name}
                  </th>
                ))}
                {result && (
                  <th className="px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left">
                    Margins
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {candidates.map((c, i) => {
                const scored = result?.candidates.find((r) => r.mpn === c.mpn.trim());
                return (
                  <tr key={i} style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                    <td className="px-2 py-1">
                      <input
                        type="radio"
                        name="selected-candidate"
                        checked={!!c.mpn.trim() && selectedMpn === c.mpn.trim()}
                        onChange={() => setSelectedMpn(c.mpn.trim())}
                        data-testid={`select-candidate-radio-${i}`}
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={c.mpn}
                        onChange={(e) =>
                          setCandidates((cs) =>
                            cs.map((row, j) => (j === i ? { ...row, mpn: e.target.value } : row)),
                          )
                        }
                        placeholder="e.g. MG996R"
                        className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                        style={{ ...FIELD_STYLE, width: '120px' }}
                      />
                    </td>
                    <td className="px-2 py-1">
                      <input
                        value={c.manufacturer}
                        onChange={(e) =>
                          setCandidates((cs) =>
                            cs.map((row, j) =>
                              j === i ? { ...row, manufacturer: e.target.value } : row,
                            ),
                          )
                        }
                        placeholder="e.g. TowerPro"
                        className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                        style={{ ...FIELD_STYLE, width: '120px' }}
                      />
                    </td>
                    {specNames.map((name) => (
                      <td key={name} className="px-2 py-1">
                        <input
                          value={c.specs[name] ?? ''}
                          onChange={(e) =>
                            setCandidates((cs) =>
                              cs.map((row, j) =>
                                j === i
                                  ? { ...row, specs: { ...row.specs, [name]: e.target.value } }
                                  : row,
                              ),
                            )
                          }
                          type="number"
                          className="rounded px-1 py-1 text-xs text-on-surface text-right focus:outline-none"
                          style={{ ...FIELD_STYLE, width: '80px' }}
                        />
                      </td>
                    ))}
                    {result && (
                      <td className="px-2 py-1 text-xs">
                        {scored ? (
                          <span
                            style={{
                              color: Object.values(scored.margins).every((m) => m.pass)
                                ? 'var(--mf-c-7ee081, #7ee081)'
                                : 'var(--mf-c-ff8a80, #ff8a80)',
                            }}
                          >
                            {Object.entries(scored.margins)
                              .map(
                                ([name, m]) =>
                                  `${name}: ${m.pass ? 'PASS' : 'FAIL'}${m.margin !== null ? ` (${m.margin >= 0 ? '+' : ''}${m.margin.toFixed(2)})` : ''}`,
                              )
                              .join(', ')}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant" style={{ flex: 1, minWidth: '240px' }}>
            Rationale
            <input
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Why this part, over the others?"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '100%' }}
            />
          </label>
          <Button
            size="sm"
            data-testid="select-component-button"
            disabled={!canSubmit || select.isPending}
            onClick={handleSubmit}
          >
            {select.isPending ? 'Recording…' : 'Select component'}
          </Button>
        </div>

        {result && (
          <div
            className="mt-3 rounded px-3 py-2 text-xs"
            data-testid="component-selection-result"
            style={{
              background: 'var(--mf-c-191b22)',
              border: '1px solid var(--mf-r-65-72-90-0p2)',
              color: result.selected_meets_requirements
                ? 'var(--mf-c-7ee081, #7ee081)'
                : 'var(--mf-c-ff8a80, #ff8a80)',
            }}
          >
            {result.selected_mpn} recorded
            {result.selected_meets_requirements
              ? ' -- meets all required specs.'
              : ' -- selected despite failing one or more required specs (see rationale).'}
          </div>
        )}
      </div>
    </div>
  );
}

export function BomPage() {
  const { activeProjectId } = useActiveProject();
  const [view, setView] = useState<BomView>('flat');
  const { data: components, isLoading: flatLoading } = useBom(activeProjectId ?? undefined);
  const { data: hierarchicalLines, isLoading: hierarchicalLoading } = useHierarchicalBom(
    activeProjectId ?? undefined,
  );
  const isLoading = view === 'flat' ? flatLoading : hierarchicalLoading;
  const bomRisk = useBomRisk();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sortField, setSortField] = useState<SortField>('designator');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const items = components ?? [];
  const totalCost = items.reduce((sum, c) => sum + c.quantity * c.unitPrice, 0);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return items.filter((c) => {
      const matchesSearch =
        !q ||
        c.designator.toLowerCase().includes(q) ||
        c.partNumber.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.manufacturer.toLowerCase().includes(q);
      const matchesStatus = !statusFilter || c.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [items, search, statusFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const av = a[sortField];
      const bv = b[sortField];
      const cmp =
        typeof av === 'number' && typeof bv === 'number'
          ? av - bv
          : String(av).localeCompare(String(bv));
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [filtered, sortField, sortDir]);

  function handleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('asc');
    }
  }

  function handleExportCsv() {
    const header = ['Ref', 'Part Number', 'Description', 'Manufacturer', 'Qty', 'Unit Price', 'Currency', 'Status', 'Purchase URL', 'Datasheet URL'];
    const rows = sorted.map((c) => [
      c.designator,
      c.partNumber,
      c.description,
      c.manufacturer,
      String(c.quantity),
      c.unitPrice.toFixed(2),
      c.priceCurrency,
      c.status,
      c.purchaseUrl ?? '',
      c.datasheetUrl ?? '',
    ]);
    const csv = [header, ...rows].map((r) => r.map((v) => `"${v}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bom.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  const SortIcon = ({ field }: { field: SortField }) => (
    <span
      className="material-symbols-outlined"
      style={{
        fontSize: '12px',
        opacity: sortField === field ? 1 : 0.4,
        color: sortField === field ? 'var(--mf-c-e2e2eb)' : 'var(--mf-c-9a9aaa)',
        verticalAlign: 'middle',
        marginLeft: '2px',
      }}
    >
      {sortField === field && sortDir === 'desc' ? 'expand_more' : sortField === field ? 'expand_less' : 'unfold_more'}
    </span>
  );

  return (
    <div>
      {/* Page header */}
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-medium text-on-surface" style={{ margin: 0 }}>
            Bill of Materials
          </h1>
          <span className="font-mono text-xs text-on-surface-variant">
            {view === 'flat'
              ? <>{items.length} components &middot; total ${totalCost.toFixed(2)}</>
              : <>{(hierarchicalLines ?? []).length} EBOM lines</>}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="flex items-center rounded overflow-hidden"
            style={{ border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            role="group"
            aria-label="BOM view"
          >
            {(['flat', 'hierarchical'] as BomView[]).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className="px-2.5 py-1 text-xs capitalize transition-colors"
                style={{
                  background: view === v ? 'var(--mf-c-282a30)' : 'transparent',
                  color: view === v ? 'var(--mf-c-e2e2eb)' : 'var(--mf-c-9a9aaa)',
                }}
              >
                {v}
              </button>
            ))}
          </div>
          {view === 'flat' && (
            <button
              type="button"
              onClick={handleExportCsv}
              className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{
                background: 'var(--mf-c-282a30)',
                border: '1px solid var(--mf-r-65-72-90-0p3)',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>download</span>
              CSV
            </button>
          )}
          {view === 'flat' && activeProjectId && (
            <button
              type="button"
              onClick={() => bomRisk.mutate(activeProjectId)}
              disabled={bomRisk.isPending}
              className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-50"
              style={{
                background: 'var(--mf-c-282a30)',
                border: '1px solid var(--mf-r-65-72-90-0p3)',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>shield</span>
              {bomRisk.isPending ? 'Scoring…' : 'Supply-chain risk'}
            </button>
          )}
        </div>
      </div>

      {/* Supply-chain risk panel (FORGE-268, gap G-C4) -- on-demand, since
          each run resolves real distributor offers for every real BOM line. */}
      {view === 'flat' && bomRisk.data && (
        <div
          className="mb-3 rounded px-3 py-2 text-xs"
          data-testid="bom-risk-panel"
          style={{
            background: 'var(--mf-c-191b22)',
            border: '1px solid var(--mf-r-65-72-90-0p2)',
          }}
        >
          <div className="flex items-center gap-3 font-mono text-on-surface">
            <span>
              Overall risk: <strong>{bomRisk.data.overallScore}</strong>/100
            </span>
            <span className="text-on-surface-variant">
              {bomRisk.data.totalParts} part(s) scored -- {bomRisk.data.criticalCount} critical,{' '}
              {bomRisk.data.highCount} high, {bomRisk.data.mediumCount} medium,{' '}
              {bomRisk.data.lowCount} low
            </span>
          </div>
          {bomRisk.data.partScores.some((p) => p.flagged) && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {bomRisk.data.partScores
                .filter((p) => p.flagged)
                .map((p) => (
                  <span
                    key={p.mpn}
                    title={p.factors.map((f) => `${f.name}: ${f.description}`).join('\n')}
                    className="rounded px-1.5 py-0.5 font-mono"
                    style={{
                      background:
                        p.riskLevel === 'critical' ? 'rgba(255, 138, 128, 0.15)' : 'rgba(255, 193, 7, 0.15)',
                      color: p.riskLevel === 'critical' ? 'var(--mf-c-ff8a80, #ff8a80)' : 'var(--mf-c-ffc107, #ffc107)',
                      border: `1px solid ${p.riskLevel === 'critical' ? 'var(--mf-c-ff8a80, #ff8a80)' : 'var(--mf-c-ffc107, #ffc107)'}`,
                    }}
                  >
                    {p.mpn} &middot; {p.riskLevel} ({p.overallScore})
                  </span>
                ))}
            </div>
          )}
        </div>
      )}
      {view === 'flat' && bomRisk.isError && (
        <div
          className="mb-3 rounded px-3 py-2 font-mono text-xs"
          style={{
            background: 'var(--mf-c-191b22)',
            border: '1px solid var(--mf-r-65-72-90-0p2)',
            color: 'var(--mf-c-ff8a80, #ff8a80)',
          }}
        >
          Supply-chain risk scoring failed.
        </div>
      )}

      {/* Toolbar (flat view only -- the hierarchical view is already structured) */}
      {view === 'flat' && (
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex items-center">
          <span
            className="material-symbols-outlined absolute left-2 pointer-events-none"
            style={{ fontSize: '14px', color: 'var(--mf-c-9a9aaa)' }}
          >
            search
          </span>
          <input
            type="text"
            placeholder="Search components…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="rounded pl-7 pr-2 py-1 text-xs text-on-surface placeholder:text-on-surface-variant focus:outline-none"
            style={{
              background: 'var(--mf-c-282a30)',
              border: '1px solid var(--mf-r-65-72-90-0p3)',
              width: '220px',
            }}
          />
        </div>
        <div className="relative flex items-center">
          <span
            className="material-symbols-outlined absolute left-2 pointer-events-none"
            style={{ fontSize: '14px', color: 'var(--mf-c-9a9aaa)' }}
          >
            filter_list
          </span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded pl-7 pr-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
            style={{
              background: 'var(--mf-c-282a30)',
              border: '1px solid var(--mf-r-65-72-90-0p3)',
              width: '160px',
            }}
          >
            <option value="">All statuses</option>
            <option value="available">Available</option>
            <option value="low_stock">Low Stock</option>
            <option value="out_of_stock">Out of Stock</option>
            <option value="alternate_needed">Alternate Needed</option>
          </select>
        </div>
        {(search || statusFilter) && (
          <span className="font-mono text-xs text-on-surface-variant">
            {sorted.length} of {items.length}
          </span>
        )}
      </div>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div
          className="rounded-lg overflow-hidden"
          style={{
            background: 'var(--mf-r-30-31-38-0p85)',
            border: '1px solid var(--mf-r-65-72-90-0p2)',
          }}
        >
          {[...Array(6)].map((_, i) => (
            <div
              key={i}
              className="animate-pulse"
              style={{
                height: '36px',
                borderBottom: '1px solid var(--mf-r-65-72-90-0p1)',
                background: i % 2 === 0 ? 'var(--mf-r-40-42-48-0p3)' : 'transparent',
              }}
            />
          ))}
        </div>
      )}

      {view === 'flat' && (
        <>
          {/* Empty state */}
          {!isLoading && items.length === 0 && (
            <EmptyState
              title="No components"
              description={
                activeProjectId
                  ? 'This project has no BOM components yet.'
                  : 'Select a project, or run an agent, to populate the bill of materials.'
              }
            />
          )}

          {/* Empty search result */}
          {!isLoading && items.length > 0 && sorted.length === 0 && (
            <EmptyState
              title="No matches"
              description="Try adjusting your search or filter."
            />
          )}

          {/* Table */}
          {!isLoading && sorted.length > 0 && (
            <div
              className="rounded-lg overflow-hidden overflow-x-auto"
              style={{
                background: 'var(--mf-r-30-31-38-0p85)',
                border: '1px solid var(--mf-r-65-72-90-0p2)',
              }}
            >
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr style={{ background: 'var(--mf-c-191b22)' }}>
                    <th
                      style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)', width: '32px' }}
                      aria-label="Image"
                    />
                    {(
                      [
                        { field: 'designator' as SortField, label: 'Ref', align: 'left' },
                        { field: 'partNumber' as SortField, label: 'Part Number', align: 'left' },
                        { field: 'description' as SortField, label: 'Description', align: 'left' },
                        { field: 'manufacturer' as SortField, label: 'Manufacturer', align: 'left' },
                        { field: 'quantity' as SortField, label: 'Qty', align: 'right' },
                        { field: 'unitPrice' as SortField, label: 'Unit Price', align: 'right' },
                        { field: 'status' as SortField, label: 'Status', align: 'left' },
                      ] as { field: SortField; label: string; align: string }[]
                    ).map(({ field, label, align }) => (
                      <th
                        key={field}
                        className={`px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant select-none ${align === 'right' ? 'text-right' : 'text-left'}`}
                        style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)', cursor: 'pointer', whiteSpace: 'nowrap' }}
                        onClick={() => handleSort(field)}
                      >
                        {label}
                        <SortIcon field={field} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((component) => (
                    <BomRow key={component.id} component={component} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {view === 'hierarchical' && (
        <>
          {/* Empty state -- FORGE-267: no product hierarchy recorded yet
              (twin.record_hierarchy_node hasn't been used for this project),
              distinct from "no BOM at all" */}
          {!isLoading && (hierarchicalLines ?? []).length === 0 && (
            <EmptyState
              title="No product hierarchy yet"
              description={
                activeProjectId
                  ? 'Build one with twin.record_hierarchy_node -- a product, its subsystems, and the parts/components each one is realized by -- to see a derived EBOM here.'
                  : 'Select a project to view its hierarchical BOM.'
              }
            />
          )}

          {/* Table */}
          {!isLoading && (hierarchicalLines ?? []).length > 0 && (
            <div
              className="rounded-lg overflow-hidden overflow-x-auto"
              style={{
                background: 'var(--mf-r-30-31-38-0p85)',
                border: '1px solid var(--mf-r-65-72-90-0p2)',
              }}
            >
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr style={{ background: 'var(--mf-c-191b22)' }}>
                    {(
                      ['Name', 'Part Number', 'Description', 'Manufacturer', 'Qty', 'Unit Cost', 'Source'] as string[]
                    ).map((label, i) => (
                      <th
                        key={label}
                        className={`px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant select-none ${i >= 4 && i <= 5 ? 'text-right' : 'text-left'}`}
                        style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)', whiteSpace: 'nowrap' }}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(hierarchicalLines ?? []).map((line) => (
                    <HierarchicalBomRow key={`${line.hierarchyNodeId}-${line.componentId}`} line={line} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <ComponentSelectionSection projectId={activeProjectId ?? undefined} />
    </div>
  );
}
