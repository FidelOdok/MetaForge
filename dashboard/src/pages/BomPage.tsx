import { useState, useMemo } from 'react';
import { EmptyState } from '../components/ui/EmptyState';
import { StatusBadge } from '../components/shared/StatusBadge';
import { useBom } from '../hooks/use-bom';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { ComponentChatPanel } from '../components/chat/integrations/ComponentChatPanel';
import type { BomComponent } from '../types/bom';

type SortField = 'designator' | 'partNumber' | 'description' | 'manufacturer' | 'quantity' | 'unitPrice' | 'status';
type SortDir = 'asc' | 'desc';

function BomRow({ component }: { component: BomComponent }) {
  const [chatOpen, setChatOpen] = useState(false);

  const chat = useScopedChat({
    scopeKind: 'bom-entry',
    entityId: component.id,
    defaultAgentCode: 'SC',
    label: component.partNumber,
  });

  return (
    <>
      <tr
        className="hover:bg-[#282a30] cursor-default"
        style={{ height: '36px', borderBottom: '1px solid rgba(65,72,90,0.1)' }}
      >
        <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
          {component.designator}
        </td>
        <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
          {component.partNumber}
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
          ${component.unitPrice.toFixed(2)}
        </td>
        <td className="px-3">
          <StatusBadge status={component.status} />
        </td>
        <td className="px-3">
          <button
            type="button"
            onClick={() => setChatOpen(!chatOpen)}
            className="flex items-center gap-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
              {chatOpen ? 'chat_bubble' : 'chat_bubble_outline'}
            </span>
          </button>
        </td>
      </tr>
      {chatOpen && (
        <tr>
          <td
            colSpan={8}
            className="px-3 py-2"
            style={{ borderBottom: '1px solid rgba(65,72,90,0.1)' }}
          >
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

export function BomPage() {
  const { data: components, isLoading } = useBom();

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
    const header = ['Ref', 'Part Number', 'Description', 'Manufacturer', 'Qty', 'Unit Price', 'Status'];
    const rows = sorted.map((c) => [
      c.designator,
      c.partNumber,
      c.description,
      c.manufacturer,
      String(c.quantity),
      c.unitPrice.toFixed(2),
      c.status,
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
        color: sortField === field ? '#e2e2eb' : '#9a9aaa',
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
            {items.length} components &middot; total ${totalCost.toFixed(2)}
          </span>
        </div>
        <button
          type="button"
          onClick={handleExportCsv}
          className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
          style={{
            background: '#282a30',
            border: '1px solid rgba(65,72,90,0.3)',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>download</span>
          CSV
        </button>
      </div>

      {/* Toolbar */}
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex items-center">
          <span
            className="material-symbols-outlined absolute left-2 pointer-events-none"
            style={{ fontSize: '14px', color: '#9a9aaa' }}
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
              background: '#282a30',
              border: '1px solid rgba(65,72,90,0.3)',
              width: '220px',
            }}
          />
        </div>
        <div className="relative flex items-center">
          <span
            className="material-symbols-outlined absolute left-2 pointer-events-none"
            style={{ fontSize: '14px', color: '#9a9aaa' }}
          >
            filter_list
          </span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded pl-7 pr-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
            style={{
              background: '#282a30',
              border: '1px solid rgba(65,72,90,0.3)',
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

      {/* Loading skeleton */}
      {isLoading && (
        <div
          className="rounded-lg overflow-hidden"
          style={{
            background: 'rgba(30,31,38,0.85)',
            border: '1px solid rgba(65,72,90,0.2)',
          }}
        >
          {[...Array(6)].map((_, i) => (
            <div
              key={i}
              className="animate-pulse"
              style={{
                height: '36px',
                borderBottom: '1px solid rgba(65,72,90,0.1)',
                background: i % 2 === 0 ? 'rgba(40,42,48,0.3)' : 'transparent',
              }}
            />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && items.length === 0 && (
        <EmptyState
          title="No components"
          description="BOM components will appear here when a project is loaded."
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
            background: 'rgba(30,31,38,0.85)',
            border: '1px solid rgba(65,72,90,0.2)',
          }}
        >
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: '#191b22' }}>
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
                    style={{ height: '32px', borderBottom: '1px solid rgba(65,72,90,0.2)', cursor: 'pointer', whiteSpace: 'nowrap' }}
                    onClick={() => handleSort(field)}
                  >
                    {label}
                    <SortIcon field={field} />
                  </th>
                ))}
                <th
                  className="px-3"
                  style={{ height: '32px', borderBottom: '1px solid rgba(65,72,90,0.2)', width: '40px' }}
                />
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
    </div>
  );
}
