import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, Factory, Home, Wrench } from 'lucide-react';
import {
  listMachineInstances,
  prepareBuild,
  putRoute,
  refusalOf,
  releaseBuild,
  setBuildStatus,
  type Build,
  type BuildStatus,
  type CreationView,
  type MakeRoute,
  type RouteChoice,
} from '../../api/endpoints/create';
import { useCreationWrite } from '../../hooks/use-creation';
import { useManufactureRelease } from '../../hooks/use-manufacture';
import type { TwinNode } from '../../types/twin';
import { BUILD_STATUS_COPY, ROUTE_COPY } from './copy';
import { wpTypeOf } from './ProductStage';

const ROUTE_ICON: Record<MakeRoute, typeof Home> = { home: Home, workshop: Wrench, external: Factory };

const PROCESSES: Record<MakeRoute, string[]> = {
  home: ['FDM 3D printing', 'Resin 3D printing', 'Laser cutting', 'Hand assembly'],
  workshop: ['FDM 3D printing', 'Resin 3D printing', 'CNC milling', 'Laser cutting', 'Sheet metal'],
  external: ['3D printing service', 'CNC machining', 'Injection moulding', 'PCB fabrication'],
};

/** A short, explainable suggestion from what the brief and the model say. */
export function recommendProcess(view: CreationView, model: TwinNode | undefined): { process: string; why: string } | null {
  const items = view.creation.brief.items;
  const context = `${items.making_context?.value ?? ''} ${items.materials?.value ?? ''}`.toLowerCase();
  const quantity = (items.quantity?.value ?? '').toLowerCase();
  if (model && wpTypeOf(model) === 'pcb_layout') return { process: 'PCB fabrication', why: 'The design is a circuit board.' };
  if (/\b(1000|thousand|mass|injection)\b/.test(quantity)) {
    return { process: 'Injection moulding', why: 'Repeat production at volume.' };
  }
  if (/aluminium|aluminum|steel|metal|brass/.test(context)) {
    return { process: 'CNC machining', why: 'The brief asks for a metal part.' };
  }
  if (/resin|sla/.test(context)) return { process: 'Resin 3D printing', why: 'The brief mentions resin printing.' };
  if (model && wpTypeOf(model) === 'cad_model') {
    return { process: 'FDM 3D printing', why: 'A single plastic part prints well and cheaply for a prototype.' };
  }
  return null;
}

interface MakePanelProps {
  projectId: string;
  view: CreationView;
  /** The engineering model a build would make (the one on stage). */
  model: TwinNode | undefined;
  modelRevision: number | null;
}

export function MakePanel({ projectId, view, model, modelRevision }: MakePanelProps) {
  const { creation } = view;
  const route = creation.route;
  const machines = useQuery({ queryKey: ['library', 'machines', 'instance'], queryFn: listMachineInstances, retry: false });
  const exportFiles = useManufactureRelease();
  const recommendation = recommendProcess(view, model);

  const saveRoute = useCreationWrite(projectId, (next: RouteChoice) =>
    putRoute(projectId, next, { expected_version: creation.version }),
  );
  const prepare = useCreationWrite(projectId, () =>
    prepareBuild(projectId, {
      expected_version: creation.version,
      revision: { work_product_id: model!.id, version: modelRevision, label: `${model!.name}${modelRevision ? ` rev ${modelRevision}` : ''}` },
    }),
  );
  const error = saveRoute.error ?? prepare.error;

  const choose = (change: Partial<RouteChoice>) => saveRoute.mutate({ ...route, ...change });
  const builds = [...creation.builds].reverse();

  return (
    <div className="cv-panel-body">
      <div className="cv-routes" role="radiogroup" aria-label="How it will be made">
        {(Object.keys(ROUTE_COPY) as MakeRoute[]).map((r) => {
          const Icon = ROUTE_ICON[r];
          return (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={route.route === r}
              className={`cv-route ${route.route === r ? 'is-on' : ''}`}
              disabled={saveRoute.isPending}
              onClick={() => choose({ route: r, process: route.route === r ? route.process : '' })}
            >
              <Icon size={20} aria-hidden="true" />
              <strong>{ROUTE_COPY[r].label}</strong>
              <span>{ROUTE_COPY[r].purpose}</span>
            </button>
          );
        })}
      </div>
      {builds.some((b) => b.status === 'preparing') && (
        <p className="cv-fineprint">Changing the route cancels a prepared build, so it is prepared again for the new route.</p>
      )}

      {route.route && (
        <section className="cv-subsection">
          <label className="cv-label" htmlFor="cv-process">
            Process
          </label>
          {recommendation && route.process !== recommendation.process && (
            <p className="cv-recommend">
              Suggested: <strong>{recommendation.process}</strong>. {recommendation.why}{' '}
              <button type="button" className="cv-link" onClick={() => choose({ process: recommendation.process })}>
                Use it
              </button>
            </p>
          )}
          <select
            id="cv-process"
            className="cv-input"
            value={route.process}
            onChange={(e) => choose({ process: e.target.value })}
          >
            <option value="">Choose a process</option>
            {[...new Set([...PROCESSES[route.route], ...(route.process ? [route.process] : [])])].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>

          {route.route !== 'external' ? (
            <>
              <label className="cv-label" htmlFor="cv-machine">
                Machine
              </label>
              {machines.data?.length ? (
                <select
                  id="cv-machine"
                  className="cv-input"
                  value={route.machine_id ?? ''}
                  onChange={(e) => choose({ machine_id: e.target.value || null })}
                >
                  <option value="">Choose a machine</option>
                  {machines.data.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name} ({m.kind}){m.location ? ` · ${m.location}` : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <p className="cv-muted">
                  {machines.isError ? 'The machine library is not reachable.' : 'No machines registered yet.'} Register one
                  in Engineering, or carry on: the build records what you used.
                </p>
              )}
              <p className="cv-fineprint">
                A saved machine is not proof it is ready. You confirm the machine and work area before release, and
                starting it is its own approval.
              </p>
            </>
          ) : (
            <>
              <label className="cv-label" htmlFor="cv-supplier">
                Manufacturer
              </label>
              <input
                id="cv-supplier"
                className="cv-input"
                placeholder="Who will make it (optional)"
                defaultValue={route.supplier}
                onBlur={(e) => e.target.value !== route.supplier && choose({ supplier: e.target.value })}
              />
              <p className="cv-fineprint">
                Quotes and orders go through a manufacturing connector, each with its own approval for sharing files and
                for spending. Without one, download the files and send them yourself.
              </p>
            </>
          )}

          <label className="cv-label" htmlFor="cv-material">
            Material
          </label>
          <input
            id="cv-material"
            className="cv-input"
            placeholder={creation.brief.items.materials?.value || 'PLA, PETG, aluminium…'}
            defaultValue={route.material}
            onBlur={(e) => e.target.value !== route.material && choose({ material: e.target.value })}
          />
        </section>
      )}

      {route.route && (
        <section className="cv-subsection">
          <h4>Builds</h4>
          {model ? (
            <div className="cv-actions">
              <button
                type="button"
                className="cv-btn cv-btn-primary"
                disabled={prepare.isPending}
                onClick={() => prepare.mutate(undefined)}
              >
                Prepare a build of {model.name}
                {modelRevision ? ` (revision ${modelRevision})` : ''}
              </button>
              {(route.route === 'external' || route.process.includes('CNC') || route.process.includes('3D')) && (
                <button
                  type="button"
                  className="cv-btn"
                  disabled={exportFiles.isPending}
                  onClick={() =>
                    exportFiles.mutate({ workProductId: model.id, process: route.process.includes('CNC') ? 'cnc' : '3d_print' })
                  }
                >
                  <Download size={14} /> Download making files
                </button>
              )}
            </div>
          ) : (
            <p className="cv-muted">A build needs a dimensioned engineering model. A concept image cannot be made.</p>
          )}
          {exportFiles.isError && <p className="cv-error">{refusalOf(exportFiles.error)}</p>}
          {builds.map((b) => (
            <BuildCard key={b.id} projectId={projectId} build={b} version={creation.version} />
          ))}
        </section>
      )}
      {error && <p className="cv-error">{refusalOf(error)}</p>}
      <p className="cv-fineprint">
        Slicing, toolpaths and sending a job to a machine are in{' '}
        <Link className="cv-link" to="/files">
          Engineering
        </Link>
        , where each machine action asks for your approval first.
      </p>
    </div>
  );
}

const NEXT_STATUS: Partial<Record<BuildStatus, Array<{ to: BuildStatus; label: string }>>> = {
  released: [
    { to: 'making', label: 'Making has started' },
    { to: 'completed', label: 'It is finished' },
    { to: 'cancelled', label: 'Cancel' },
  ],
  making: [
    { to: 'completed', label: 'It is finished' },
    { to: 'failed', label: 'It failed' },
    { to: 'cancelled', label: 'Cancel' },
  ],
  preparing: [{ to: 'cancelled', label: 'Cancel' }],
};

function BuildCard({ projectId, build, version }: { projectId: string; build: Build; version: number }) {
  const [ready, setReady] = useState(false);
  const release = useCreationWrite(projectId, () => releaseBuild(projectId, build.id, { expected_version: version }));
  const status = useCreationWrite(projectId, (to: BuildStatus) =>
    setBuildStatus(projectId, build.id, to, '', { expected_version: version }),
  );
  const error = release.error ?? status.error;
  const estimates = Object.entries(build.estimates);

  return (
    <article className={`cv-card cv-build is-${build.status}`}>
      <header>
        <strong>Build {build.number}</strong>
        <span className="cv-tag">{BUILD_STATUS_COPY[build.status]}</span>
      </header>
      <p className="cv-muted">
        {build.revision.label || build.revision.work_product_id} · {ROUTE_COPY[build.route].label}
        {build.process ? ` · ${build.process}` : ''}
        {build.material ? ` · ${build.material}` : ''}
      </p>
      {estimates.length ? (
        <ul className="cv-estimates">
          {estimates.map(([k, e]) => (
            <li key={k}>
              {k}: {e.value} <span className="cv-muted">({e.basis || 'basis not given'})</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="cv-fineprint">No time, material or cost estimate yet.</p>
      )}
      {build.release && (
        <p className="cv-fineprint">
          Released by {build.release.approver}
          {build.release.approver_verified ? '' : ' (identity not verified)'}. Its instructions are fixed; a change
          needs a new build.
        </p>
      )}
      {build.status === 'preparing' && (
        <>
          <label className="cv-check">
            <input type="checkbox" checked={ready} onChange={(e) => setReady(e.target.checked)} />
            {build.route === 'external'
              ? 'I have checked the files and the manufacturer’s requirements.'
              : 'I have checked the machine, the material and the work area.'}
          </label>
          <button
            type="button"
            className="cv-btn cv-btn-primary"
            disabled={!ready || release.isPending}
            onClick={() => release.mutate(undefined)}
          >
            Release build {build.number} for making
          </button>
        </>
      )}
      <div className="cv-actions">
        {(NEXT_STATUS[build.status] ?? []).map((n) => (
          <button key={n.to} type="button" className="cv-btn" disabled={status.isPending} onClick={() => status.mutate(n.to)}>
            {n.label}
          </button>
        ))}
      </div>
      {error && <p className="cv-error">{refusalOf(error)}</p>}
    </article>
  );
}
