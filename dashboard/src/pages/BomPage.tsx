import { useState, useMemo, useCallback } from 'react';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonTable } from '../components/ui/Skeleton';
import { useBom } from '../hooks/use-bom';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { ComponentChatPanel } from '../components/chat/integrations/ComponentChatPanel';
import type { BomComponent } from '../types/bom';

// ─── Types ────────────────────────────────────────────────────────────────────

type SortDir = 'asc' | 'desc';

interface SortState {
  column: keyof BomComponent;
  dir: SortDir;
}

// ─── Sort helpers ─────────────────────────────────────────────────────────────

const SORTABLE_COLUMNS: { key: keyof BomComponent; label: string; align?: 'right' }[] = [
  { key: 'designator', label: 'Ref' },
  { key: 'partNumber', label: 'Part Number' },
  { key: 'description', label: 'Description' },
  { key: 'manufacturer', label: 'Manufacturer' },
  { key: 'quantity', label: 'Qty', align: 'right' },
  { key: 'unitPrice', label: 'Unit Price', align: 'right' },
  { key: 'status', label: 'Status' },
];

function sortItems(items: BomComponent[], sort: SortState | null): BomComponent[] {
  if (!sort) return items;
  return [...items].sort((a, b) => {
    const aVal = a[sort.column];
    const bVal = b[sort.column];
    let cmp = 0;
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      cmp = aVal - bVal;
    } else {
      cmp = String(aVal).localeCompare(String(bVal));
    }
    return sort.dir === 'asc' ? cmp : -cmp;
  });
}

function formatCurrency(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

// ─── CSV export ───────────────────────────────────────────────────────────────

function exportCsv(items: BomComponent[]) {
  const headers = ['Designator', 'Part Number', 'Description', 'Manufacturer', 'Quantity', 'Unit Price', 'Extended Cost', 'Status', 'Category'];
  const rows = items.map((c) => [
    c.designator,
    c.partNumber,
    c.description,
    c.manufacturer,
    String(c.quantity),
    c.unitPrice.toFixed(2),
    (c.unitPrice * c.quantity).toFixed(2),
    c.status,
    c.category,
  ]);
  const csv = [headers, ...rows]
    .map((row) => row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bom-export.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// ─── BomRow ───────────────────────────────────────────────────────────────────

interface BomRowProps {
  component: BomComponent;
  isExpanded: boolean;
  onToggleExpand: (id: string) => void;
}

function BomRow({ component, isExpanded, onToggleExpand }: BomRowProps) {
  const [chatOpen, setChatOpen] = useState(false);

  const chat = useScopedChat({
    scopeKind: 'bom-entry',
    entityId: component.id,
    defaultAgentCode: 'SC',
    label: component.partNumber,
  });

  const extendedCost = component.unitPrice * component.quantity;

  return (
    <>
      <tr
        className="cursor-pointer border-b border-zinc-100 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/40"
        onClick={() => onToggleExpand(component.id)}
      >
        <td className="px-3 py-2 text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {component.designator}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300">
          {component.partNumber}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-600 dark:text-zinc-400">
          {component.description}
        </td>
        <td className="px-3 py-2 text-sm text-zinc-600 dark:text-zinc-400">
          {component.manufacturer}
        </td>
        <td className="px-3 py-2 text-right text-sm text-zinc-700 dark:text-zinc-300">
          {component.quantity}
        </td>
        <td className="px-3 py-2 text-right text-sm text-zinc-700 dark:text-zinc-300">
          {formatCurrency(component.unitPrice)}
        </td>
        <td className="px-3 py-2 text-right text-sm text-zinc-500 dark:text-zinc-400">
          {formatCurrency(extendedCost)}
        </td>
        <td className="px-3 py-2">
          <StatusBadge status={component.status} />
        </td>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            onClick={() => setChatOpen(!chatOpen)}
            className="text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            {chatOpen ? 'Hide' : 'Chat'}
          </button>
        </td>
      </tr>

      {isExpanded && (
        <tr className="bg-zinc-50 dark:bg-zinc-800/30">
          <td colSpan={9} className="px-6 py-3 text-sm text-zinc-600 dark:text-zinc-400">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <div>
                <span className="font-medium text-zinc-700 dark:text-zinc-300">Category: </span>
                {component.category}
              </div>
              <div>
                <span className="font-medium text-zinc-700 dark:text-zinc-300">Project ID: </span>
                {component.projectId}
              </div>
              <div>
                <span className="font-medium text-zinc-700 dark:text-zinc-300">Component ID: </span>
                {component.id}
              </div>
            </div>
          </td>
        </tr>
      )}

      {chatOpen && (
        <tr>
          <td colSpan={9} className="px-3 py-2">
            <ComponentChatPanel
              componentId={component.id}
              componentName={component.partNumber}
              thread={chat.thread}
              messages={chat.messages}
              isTyping={chat.isTyping}
              onSendMessage={chat.sendMessage}
              onCreateThread={chat.createThread}
            />
          </td>
        </tr>
      )}
    </>
  );
}

// ─── Sort indicator ───────────────────────────────────────────────────────────

function SortIndicator({ col, sort }: { col: keyof BomComponent; sort: SortState | null }) {
  if (!sort || sort.column !== col) {
    return <span className="ml-1 text-zinc-300 dark:text-zinc-600">↕</span>;
  }
  return (
    <span className="ml-1 text-blue-500">
      {sort.dir === 'asc' ? '▲' : '▼'}
    </span>
  );
}

// ─── BomPage ──────────────────────────────────────────────────────────────────

export function BomPage() {
  const { data: components, isLoading, isError, refetch } = useBom();

  // Search
  const [search, setSearch] = useState('');

  // Column filters
  const [filterStatus, setFilterStatus] = useState('');
  const [filterManufacturer, setFilterManufacturer] = useState('');
  const [filterCategory, setFilterCategory] = useState('');

  // Sort
  const [sort, setSort] = useState<SortState | null>(null);

  // Row expansion
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const handleSort = useCallback((col: keyof BomComponent) => {
    setSort((prev) => {
      if (prev?.column === col) {
        return { column: col, dir: prev.dir === 'asc' ? 'desc' : 'asc' };
      }
      return { column: col, dir: 'asc' };
    });
  }, []);

  const allItems = components ?? [];

  // Derive unique filter options
  const statusOptions = useMemo(
    () => [...new Set(allItems.map((c) => c.status))].sort(),
    [allItems]
  );
  const manufacturerOptions = useMemo(
    () => [...new Set(allItems.map((c) => c.manufacturer))].filter(Boolean).sort(),
    [allItems]
  );
  const categoryOptions = useMemo(
    () => [...new Set(allItems.map((c) => c.category))].filter(Boolean).sort(),
    [allItems]
  );

  // Filtered + sorted list
  const visibleItems = useMemo(() => {
    const q = search.toLowerCase();
    let result = allItems;

    if (q) {
      result = result.filter(
        (c) =>
          c.partNumber.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q) ||
          c.manufacturer.toLowerCase().includes(q) ||
          c.designator.toLowerCase().includes(q)
      );
    }

    if (filterStatus) {
      result = result.filter((c) => c.status === filterStatus);
    }
    if (filterManufacturer) {
      result = result.filter((c) => c.manufacturer === filterManufacturer);
    }
    if (filterCategory) {
      result = result.filter((c) => c.category === filterCategory);
    }

    return sortItems(result, sort);
  }, [allItems, search, filterStatus, filterManufacturer, filterCategory, sort]);

  // Cost rollup
  const totalUnitCost = useMemo(
    () => visibleItems.reduce((sum, c) => sum + c.unitPrice, 0),
    [visibleItems]
  );
  const totalExtendedCost = useMemo(
    () => visibleItems.reduce((sum, c) => sum + c.unitPrice * c.quantity, 0),
    [visibleItems]
  );

  if (isLoading) {
    return (
      <div data-testid="loading-skeleton">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            Bill of Materials
          </h2>
        </div>
        <SkeletonTable rows={8} cols={5} />
      </div>
    );
  }

  if (isError) {
    return (
      <div>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            Bill of Materials
          </h2>
        </div>
        <Card className="flex flex-col items-center py-12 text-center">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Failed to load BOM
          </p>
          <p className="mt-1 text-sm text-zinc-500">
            There was a problem fetching the bill of materials.
          </p>
          <Button variant="secondary" className="mt-4" onClick={() => void refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Bill of Materials
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-sm text-zinc-500">
            {visibleItems.length} / {allItems.length} components
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => exportCsv(visibleItems)}
            disabled={visibleItems.length === 0}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {/* Toolbar: search + column filters */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          type="search"
          placeholder="Search part, description, manufacturer…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 flex-1 rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          aria-label="Search BOM"
        />
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          aria-label="Filter by status"
          className="h-8 rounded-md border border-zinc-200 bg-white px-2 text-sm text-zinc-700 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
        >
          <option value="">All statuses</option>
          {statusOptions.map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
          ))}
        </select>
        <select
          value={filterManufacturer}
          onChange={(e) => setFilterManufacturer(e.target.value)}
          aria-label="Filter by manufacturer"
          className="h-8 rounded-md border border-zinc-200 bg-white px-2 text-sm text-zinc-700 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
        >
          <option value="">All manufacturers</option>
          {manufacturerOptions.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
          aria-label="Filter by category"
          className="h-8 rounded-md border border-zinc-200 bg-white px-2 text-sm text-zinc-700 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
        >
          <option value="">All categories</option>
          {categoryOptions.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {(search || filterStatus || filterManufacturer || filterCategory) && (
          <button
            type="button"
            onClick={() => {
              setSearch('');
              setFilterStatus('');
              setFilterManufacturer('');
              setFilterCategory('');
            }}
            className="text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            Clear filters
          </button>
        )}
      </div>

      {allItems.length === 0 ? (
        <EmptyState
          title="No BOM entries"
          description="BOM components will appear here when a project is loaded."
        />
      ) : visibleItems.length === 0 ? (
        <EmptyState
          title="No matching components"
          description="Try adjusting your search or filters."
        />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-left">
            <thead className="sticky top-0 z-10 bg-white dark:bg-zinc-900">
              <tr className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800/50">
                {SORTABLE_COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    className={`cursor-pointer select-none px-3 py-2 text-xs font-medium uppercase text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 ${col.align === 'right' ? 'text-right' : ''}`}
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                    <SortIndicator col={col.key} sort={sort} />
                  </th>
                ))}
                {/* Extended cost column header */}
                <th
                  className="cursor-pointer select-none px-3 py-2 text-right text-xs font-medium uppercase text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                  onClick={() => handleSort('quantity')}
                >
                  Ext. Cost
                </th>
                <th className="px-3 py-2 text-xs font-medium uppercase text-zinc-500"></th>
              </tr>
            </thead>
            <tbody>
              {visibleItems.map((component) => (
                <BomRow
                  key={component.id}
                  component={component}
                  isExpanded={expandedIds.has(component.id)}
                  onToggleExpand={toggleExpand}
                />
              ))}
            </tbody>
            <tfoot className="sticky bottom-0 z-10 bg-white dark:bg-zinc-900">
              <tr className="border-t-2 border-zinc-200 bg-zinc-50 font-medium dark:border-zinc-700 dark:bg-zinc-800/50">
                <td colSpan={4} className="px-3 py-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                  Total ({visibleItems.length} items)
                </td>
                <td className="px-3 py-2 text-right text-sm text-zinc-500 dark:text-zinc-400">
                  —
                </td>
                <td className="px-3 py-2 text-right text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                  {formatCurrency(totalUnitCost)}
                </td>
                <td className="px-3 py-2 text-right text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                  {formatCurrency(totalExtendedCost)}
                </td>
                <td colSpan={2} />
              </tr>
            </tfoot>
          </table>
        </Card>
      )}
    </div>
  );
}
