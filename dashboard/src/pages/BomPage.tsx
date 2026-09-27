import { useState, useMemo } from 'react';
import { EmptyState } from '../components/ui/EmptyState';
import { StatusBadge } from '../components/shared/StatusBadge';
import { useBom, useHierarchicalBom } from '../hooks/use-bom';
import { useActiveProject } from '../hooks/use-active-project';
import type { BomComponent, HierarchicalBomLine } from '../types/bom';

type BomView = 'flat' | 'hierarchical';

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

export function BomPage() {
  const { activeProjectId } = useActiveProject();
  const [view, setView] = useState<BomView>('flat');
  const { data: components, isLoading: flatLoading } = useBom(activeProjectId ?? undefined);
  const { data: hierarchicalLines, isLoading: hierarchicalLoading } = useHierarchicalBom(
    activeProjectId ?? undefined,
  );
  const isLoading = view === 'flat' ? flatLoading : hierarchicalLoading;

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
        </div>
      </div>

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
    </div>
  );
}
