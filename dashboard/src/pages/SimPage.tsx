import { useRef, useState } from 'react';
import { EmptyState } from '../components/ui/EmptyState';
import { LoadCaseDialog, type LoadCaseDialogHandle } from '../components/simulation/LoadCaseDialog';
import { ResultsCompare } from '../components/simulation/ResultsCompare';
import { SimFieldPanel } from '../components/simulation/SimFieldPanel';
import { FieldCompare } from '../components/simulation/FieldCompare';
import { MeshConvergenceChart } from '../components/simulation/MeshConvergenceChart';
import { useLoadCases } from '../hooks/use-load-cases';
import { useSimulationResults } from '../hooks/use-simulation-results';
import { useActiveProject } from '../hooks/use-active-project';
import type { LoadCase } from '../types/loadCase';
import type { SimulationResult } from '../types/simulationResult';

function formatForce(force: [number, number, number] | null): string {
  if (!force) return '—';
  const [x, y, z] = force;
  return `${x}, ${y}, ${z} N`;
}

function formatMpa(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(1)} MPa`;
}

function formatMm(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(3)} mm`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

function LoadCaseRow({ loadCase }: { loadCase: LoadCase }) {
  return (
    <tr
      className="hover:bg-[#282a30] cursor-default"
      style={{ height: '36px', borderBottom: '1px solid rgba(65,72,90,0.1)' }}
    >
      <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">{loadCase.name}</td>
      <td className="px-3 text-xs text-on-surface-variant whitespace-nowrap">
        {(loadCase.material?.name as string | undefined) ?? '—'}
      </td>
      <td className="px-3 font-mono text-xs text-on-surface-variant whitespace-nowrap">
        {loadCase.fixedNodeSet ?? '—'}
      </td>
      <td className="px-3 font-mono text-xs text-on-surface-variant whitespace-nowrap">
        {loadCase.loadNodeSet ?? '—'}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface whitespace-nowrap">
        {formatForce(loadCase.loadForceN)}
      </td>
      <td className="px-3 text-xs text-on-surface-variant">{loadCase.sourceOfLoads ?? '—'}</td>
    </tr>
  );
}

function ResultRow({
  result,
  checked,
  onToggle,
  focused,
  onFocus,
}: {
  result: SimulationResult;
  checked: boolean;
  onToggle: (checked: boolean) => void;
  focused: boolean;
  onFocus: () => void;
}) {
  return (
    <tr
      className="hover:bg-[#282a30] cursor-pointer"
      aria-selected={focused}
      onClick={onFocus}
      style={{
        height: '36px',
        borderBottom: '1px solid rgba(65,72,90,0.1)',
        background: focused ? 'rgba(230,126,34,0.08)' : undefined,
        boxShadow: focused ? 'inset 2px 0 0 #e67e22' : undefined,
      }}
    >
      <td className="px-3">
        <input
          type="checkbox"
          aria-label={`Select ${result.name} for comparison`}
          checked={checked}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onToggle(e.target.checked)}
        />
      </td>
      <td className="px-3 font-mono text-xs text-on-surface whitespace-nowrap">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onFocus();
          }}
          className="text-left hover:text-primary-container"
          aria-label={`Open ${result.name} in 3D`}
        >
          {result.name}
        </button>
      </td>
      <td className="px-3 text-xs whitespace-nowrap" title={result.hasField ? '3D field stored' : 'Field not stored'}>
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '14px', color: result.hasField ? '#86cfff' : 'rgba(154,154,170,0.4)', verticalAlign: 'middle' }}
        >
          {result.hasField ? 'view_in_ar' : 'block'}
        </span>
        <span className="sr-only">{result.hasField ? '3D field stored' : 'Field not stored'}</span>
      </td>
      <td className="px-3 text-xs text-on-surface-variant whitespace-nowrap">
        {result.loadCase ?? '—'}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface whitespace-nowrap">
        {formatMpa(result.maxVonMisesMpa)}
      </td>
      <td className="px-3 text-right font-mono text-xs text-on-surface whitespace-nowrap">
        {formatMm(result.maxDisplacementMm)}
      </td>
      <td className="px-3 text-xs text-on-surface-variant whitespace-nowrap">
        {formatDate(result.createdAt)}
      </td>
    </tr>
  );
}

function geometryLabel(result: SimulationResult): string | null {
  const g = result.analysedGeometry;
  if (!g) return null;
  const name = g.name ?? g.node_id.slice(0, 8);
  return g.revision !== undefined ? `${name} rev ${g.revision}` : name;
}

/** FORGE-532: one result in 3D (field viewer or the "field not stored"
 * note), what it analysed, and its mesh-convergence chart if recorded. */
function ResultDetail({ result }: { result: SimulationResult }) {
  const geometry = geometryLabel(result);
  const fixtures = (result.fixtures ?? []).map((f) => String(f.label ?? f.kind ?? '')).filter(Boolean);
  return (
    <div data-testid="result-detail" className="mt-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h3 className="font-mono text-xs text-on-surface" style={{ margin: 0 }}>
          {result.name}
        </h3>
        {geometry && (
          <span className="font-mono text-[10px] text-on-surface-variant">Geometry · {geometry}</span>
        )}
        {result.loadCase && (
          <span className="font-mono text-[10px] text-on-surface-variant">Load case · {result.loadCase}</span>
        )}
        {fixtures.length > 0 && (
          <span className="font-mono text-[10px] text-on-surface-variant">Fixtures · {fixtures.join(', ')}</span>
        )}
      </div>
      <SimFieldPanel resultId={result.id} hasField={result.hasField} title={result.name} />
      {result.meshConvergence && <MeshConvergenceChart convergence={result.meshConvergence} />}
    </div>
  );
}

/** FORGE-278/FORGE-279: Sim tab — load cases as reusable work products, and
 * the FEA results (persisted by FORGE-246) they produced, comparable
 * side by side. */
export function SimPage() {
  const { activeProjectId } = useActiveProject();
  const { data: loadCases, isLoading } = useLoadCases(activeProjectId ?? undefined);
  const { data: results, isLoading: resultsLoading } = useSimulationResults(
    activeProjectId ?? undefined,
  );
  const [selectedResultIds, setSelectedResultIds] = useState<string[]>([]);
  const [focusedResultId, setFocusedResultId] = useState<string | null>(null);
  const dialogRef = useRef<LoadCaseDialogHandle>(null);
  const newLoadCaseButtonRef = useRef<HTMLButtonElement>(null);

  const items = loadCases ?? [];
  const resultItems = results ?? [];

  const toggleResult = (id: string, checked: boolean) => {
    setSelectedResultIds((prev) => {
      if (checked) {
        // At most two selected at a time — a third pick replaces the
        // oldest, so comparison is always exactly "these two".
        const next = [...prev, id];
        return next.length > 2 ? next.slice(1) : next;
      }
      return prev.filter((existing) => existing !== id);
    });
  };

  // Oldest first, so the compare panel reads as "change from A to B"
  // regardless of which row was checked first.
  // FORGE-532: the result open in 3D, defaulting to the newest one.
  const focusedResult =
    resultItems.find((r) => r.id === focusedResultId) ?? resultItems[0] ?? null;

  const selectedResults = resultItems
    .filter((r) => selectedResultIds.includes(r.id))
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt));

  return (
    <div>
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-medium text-on-surface" style={{ margin: 0 }}>
            Simulation
          </h1>
          <span className="font-mono text-xs text-on-surface-variant">
            {items.length} load case{items.length === 1 ? '' : 's'}
          </span>
        </div>
        {activeProjectId && (
          <button
            ref={newLoadCaseButtonRef}
            type="button"
            onClick={() => dialogRef.current?.open()}
            className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
            style={{ background: '#282a30', border: '1px solid rgba(65,72,90,0.3)' }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
              add
            </span>
            New load case
          </button>
        )}
      </div>

      {isLoading && (
        <div
          className="rounded-lg overflow-hidden"
          style={{ background: 'rgba(30,31,38,0.85)', border: '1px solid rgba(65,72,90,0.2)' }}
        >
          {[...Array(4)].map((_, i) => (
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

      {!isLoading && !activeProjectId && (
        <EmptyState
          title="No project selected"
          description="Select a project to view or define its load cases."
        />
      )}

      {!isLoading && activeProjectId && items.length === 0 && (
        <EmptyState
          title="No load cases"
          description="Define a boundary condition once, and reuse it across design versions."
        />
      )}

      {!isLoading && items.length > 0 && (
        <div
          className="rounded-lg overflow-hidden overflow-x-auto"
          style={{ background: 'rgba(30,31,38,0.85)', border: '1px solid rgba(65,72,90,0.2)' }}
        >
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: '#191b22' }}>
                {[
                  { label: 'Name', align: 'left' },
                  { label: 'Material', align: 'left' },
                  { label: 'Fixed Node Set', align: 'left' },
                  { label: 'Load Node Set', align: 'left' },
                  { label: 'Force', align: 'right' },
                  { label: 'Source of Loads', align: 'left' },
                ].map(({ label, align }) => (
                  <th
                    key={label}
                    className={`px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant select-none ${align === 'right' ? 'text-right' : 'text-left'}`}
                    style={{ height: '32px', borderBottom: '1px solid rgba(65,72,90,0.2)', whiteSpace: 'nowrap' }}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((loadCase) => (
                <LoadCaseRow key={loadCase.id} loadCase={loadCase} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {activeProjectId && (
        <LoadCaseDialog ref={dialogRef} projectId={activeProjectId} returnFocusRef={newLoadCaseButtonRef} />
      )}

      {activeProjectId && (
        <div className="mt-8">
          <div className="mb-3">
            <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
              Results
            </h2>
            <span className="font-mono text-xs text-on-surface-variant">
              {resultItems.length} result{resultItems.length === 1 ? '' : 's'}
              {selectedResultIds.length === 2 ? ' · 2 selected for comparison' : ''}
            </span>
          </div>

          {!resultsLoading && resultItems.length === 0 && (
            <EmptyState
              title="No results yet"
              description="Run an analysis (calculix.run_fea + extract_results) and record it as a simulation_result to see it here."
            />
          )}

          {!resultsLoading && resultItems.length > 0 && (
            <div
              className="rounded-lg overflow-hidden overflow-x-auto"
              style={{ background: 'rgba(30,31,38,0.85)', border: '1px solid rgba(65,72,90,0.2)' }}
            >
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr style={{ background: '#191b22' }}>
                    {[
                      { label: '', align: 'left' },
                      { label: 'Name', align: 'left' },
                      { label: '3D', align: 'left' },
                      { label: 'Load Case', align: 'left' },
                      { label: 'Max Stress', align: 'right' },
                      { label: 'Max Displacement', align: 'right' },
                      { label: 'Created', align: 'left' },
                    ].map(({ label, align }) => (
                      <th
                        key={label || 'select'}
                        className={`px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant select-none ${align === 'right' ? 'text-right' : 'text-left'}`}
                        style={{ height: '32px', borderBottom: '1px solid rgba(65,72,90,0.2)', whiteSpace: 'nowrap' }}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {resultItems.map((result) => (
                    <ResultRow
                      key={result.id}
                      result={result}
                      checked={selectedResultIds.includes(result.id)}
                      onToggle={(checked) => toggleResult(result.id, checked)}
                      focused={focusedResult?.id === result.id}
                      onFocus={() => setFocusedResultId(result.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(() => {
            const [first, second] = selectedResults;
            return first && second ? (
              <>
                <ResultsCompare a={first} b={second} />
                <FieldCompare a={first} b={second} />
              </>
            ) : null;
          })()}

          {focusedResult && selectedResults.length < 2 && (
            <ResultDetail key={focusedResult.id} result={focusedResult} />
          )}
        </div>
      )}
    </div>
  );
}
