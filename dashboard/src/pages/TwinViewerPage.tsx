import { useState, useRef, useCallback, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Activity,
  Box,
  Camera,
  ChevronDown,
  Clock3,
  Columns2,
  Layers,
  Maximize2,
  MessageSquare,
  Minimize2,
  Network,
  PanelLeftClose,
  PanelRightClose,
  Search,
  Upload,
  X,
} from 'lucide-react';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import { useTwinNodes, useTwinNode, useTwinRelationships, useNodeVersionHistory } from '../hooks/use-twin';
import { useActiveProject } from '../hooks/use-active-project';
import { R3FViewer } from '../components/viewer/R3FViewer';
import { ComponentTree } from '../components/viewer/ComponentTree';
import { TwinGraphCanvas } from '../components/viewer/TwinGraphCanvas';
import { BomAnnotationPanel } from '../components/viewer/BomAnnotationPanel';
import { NodeProposals } from '../components/viewer/NodeProposals';
import { ExplodedViewControls } from '../components/viewer/ExplodedViewControls';
import { AssemblyExportPanel } from '../components/viewer/AssemblyExportPanel';
import { TwinAgentChat } from '../components/viewer/TwinAgentChat';
import { useViewerStore } from '../store/viewer-store';
import { useLayoutStore } from '../store/layout-store';
import { useUploadAndConvert } from '../hooks/use-conversion';
import { getNodeModel, nodeFileUrl } from '../api/endpoints/twin';
import { FullScreenPreviewModal } from '../components/viewer/FullScreenPreviewModal';
import { iconForNode } from '../utils/wp-icons';
import { toDownloadHref, type ExportFile } from '../api/endpoints/cad-export';
import { useExportUrdf, useExportSdf, useExportUsd } from '../hooks/use-cad-export';
import { useToast } from '../components/ui/Toast';
import type { TwinNode } from '../types/twin';
import type { ModelManifest, PartInfo, PartTreeNode } from '../types/viewer';
import { resolveGatewayHref } from '../lib/gatewayConfig';
import apiClient from '../api/client';
import { installSampleAdapter, isSampleMode, SAMPLE_PROJECT_ID, SAMPLE_PROJECT_NAME } from '../lib/sample-workspace';
import './TwinViewerPage.css';

// `?demo=1` serves the illustrative sample workspace offline. Idempotent, so
// it is harmless if the shared client already installs it.
installSampleAdapter(apiClient);


// MET-720: names the cadquery adapter's material density table
// (tool_registry/tools/cadquery/materials.py) actually recognizes.
const CAD_EXPORT_MATERIALS = [
  'aluminum_6061', 'aluminum', 'steel', 'stainless_steel', 'titanium', 'brass',
  'copper', 'abs', 'pla', 'petg', 'nylon', 'polycarbonate', 'acrylic', 'wood',
  'carbon_fiber', 'rubber',
];

// MET-683: same response.data.detail extraction pattern as
// ProjectDetailPage.tsx's getErrorMessage, applied to a failed model load.
function getModelLoadErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return 'This work product has no viewable 3D model yet.';
}

// ── KC tokens ────────────────────────────────────────────────────────────────
const KC = {
  surface: 'var(--mf-c-111319)',
  surfaceLow: 'var(--mf-c-191b22)',
  surfaceContainer: 'var(--mf-r-30-31-38-0p88)',
  surfaceHigh: 'var(--mf-c-282a30)',
  border: 'var(--mf-r-65-72-90-0p2)',
  borderMid: 'var(--mf-r-65-72-90-0p3)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  orange: '#ff5a0a',
  orangeFaint: 'rgba(255, 90, 10,0.15)',
  orangeBorder: 'rgba(255, 90, 10,0.45)',
  teal: 'var(--mf-c-86cfff)',
  green: 'var(--mf-c-3dd68c)',
  statusBar: 'var(--mf-r-12-14-20-0p95)',
} as const;

// MET-747 follow-up: format-kind detection now lives in FullScreenPreviewModal
// (the "Preview" action opens that instead of a cramped 320px inline strip).

function FileActionBtn({
  icon,
  label,
  onClick,
}: {
  icon: string;
  label: string;
  onClick?: () => void;
}) {
  return (
    <Button variant="secondary" size="sm" className="text-xs" onClick={onClick}>
      <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>{icon}</span>
      {label}
    </Button>
  );
}

function WorkProductFileSection({ node }: { node: TwinNode }) {
  const wpType = node.properties.wp_type ? String(node.properties.wp_type) : undefined;
  const filePath = node.properties.file_path ? String(node.properties.file_path) : '';
  const fmt = (node.properties.format ? String(node.properties.format) : '').toLowerCase();
  const [fullScreen, setFullScreen] = useState(false);

  const inlineUrl = nodeFileUrl(node.id, false);
  const downloadUrl = nodeFileUrl(node.id, true);
  const isSketch = wpType === 'design_sketch';
  const needsApproval = isSketch && node.properties.approved !== true;

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
        File
      </div>
      <div className="flex items-center gap-2 mb-1" style={{ flexWrap: 'wrap' }}>
        <span
          className="font-mono"
          style={{ fontSize: 10, color: KC.orange, background: 'rgba(255, 90, 10,0.1)', padding: '2px 6px', borderRadius: 3, textTransform: 'uppercase', letterSpacing: '0.06em' }}
        >
          {wpType ?? 'unknown'}
        </span>
        {fmt && <span className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>.{fmt}</span>}
        {needsApproval && (
          <span
            className="font-mono uppercase"
            style={{ fontSize: 9, color: 'var(--mf-c-f5b04d)', background: 'rgba(245,176,77,0.14)', padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em' }}
          >
            Needs approval
          </span>
        )}
      </div>
      <div
        className="font-mono mb-2"
        title={filePath || undefined}
        style={{ fontSize: 11, color: filePath ? KC.onSurface : KC.onSurfaceVariant, wordBreak: 'break-all' }}
      >
        {filePath || 'No file path on record'}
      </div>
      <div className="flex gap-1.5">
        <a href={downloadUrl} download style={{ textDecoration: 'none' }}><FileActionBtn icon="download" label="Download" /></a>
        <a href={inlineUrl} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}><FileActionBtn icon="open_in_new" label="Open" /></a>
        <FileActionBtn icon="fullscreen" label="Preview" onClick={() => setFullScreen(true)} />
      </div>
      {fullScreen && <FullScreenPreviewModal node={node} onClose={() => setFullScreen(false)} />}
    </div>
  );
}

// ── Export for robotics sim (MET-720) ────────────────────────────────────────
const _EXPORT_FORMATS = ['urdf', 'sdf', 'usd'] as const;
type _ExportFormat = (typeof _EXPORT_FORMATS)[number];

const _exportInputStyle: React.CSSProperties = {
  fontSize: 11,
  background: 'var(--mf-c-1e1f26)',
  border: `1px solid ${KC.border}`,
  color: KC.onSurface,
};

function ExportForSimSection({ node, onClose }: { node: TwinNode; onClose: () => void }) {
  const toast = useToast();
  const urdfExport = useExportUrdf();
  const sdfExport = useExportSdf();
  const usdExport = useExportUsd();

  const [format, setFormat] = useState<_ExportFormat>('urdf');
  const [material, setMaterial] = useState('');
  const [density, setDensity] = useState('');
  const [linkName, setLinkName] = useState('base_link');
  const [modelName, setModelName] = useState('model');
  const [primName, setPrimName] = useState('model');
  const [xacro, setXacro] = useState(false);
  const [worldName, setWorldName] = useState('');
  const [staticFlag, setStaticFlag] = useState(false);
  const [result, setResult] = useState<{ outputFile: ExportFile; meshFile: ExportFile } | null>(null);

  const pending = urdfExport.isPending || sdfExport.isPending || usdExport.isPending;
  const densityKgM3 = density.trim() ? Number(density) : undefined;

  const handleSubmit = () => {
    setResult(null);
    const onSuccess = (data: { output_file: ExportFile; mesh_file: ExportFile }) => {
      setResult({ outputFile: data.output_file, meshFile: data.mesh_file });
      toast.success(`Exported ${data.output_file.filename}`);
    };
    const onError = () => toast.error(`${format.toUpperCase()} export failed`);

    if (format === 'urdf') {
      urdfExport.mutate(
        {
          node_id: node.id,
          link_name: linkName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
          xacro,
        },
        { onSuccess, onError },
      );
    } else if (format === 'sdf') {
      sdfExport.mutate(
        {
          node_id: node.id,
          model_name: modelName || undefined,
          link_name: linkName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
          static: staticFlag,
          world_name: worldName || undefined,
        },
        { onSuccess, onError },
      );
    } else {
      usdExport.mutate(
        {
          node_id: node.id,
          prim_name: primName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
        },
        { onSuccess, onError },
      );
    }
  };

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          Export for robotics sim
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 2 }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex gap-1 mb-2">
        {_EXPORT_FORMATS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => { setFormat(f); setResult(null); }}
            className="font-mono rounded px-2 py-1 uppercase"
            style={{
              fontSize: 10,
              background: format === f ? KC.orangeFaint : 'transparent',
              border: `1px solid ${format === f ? KC.orangeBorder : KC.border}`,
              color: format === f ? KC.orange : KC.onSurfaceVariant,
              cursor: 'pointer',
            }}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-1.5 mb-2">
        <div className="flex gap-1.5">
          <select
            value={material}
            onChange={(e) => setMaterial(e.target.value)}
            className="font-mono rounded px-2 py-1 flex-1"
            style={_exportInputStyle}
          >
            <option value="">No material (density only)</option>
            {CAD_EXPORT_MATERIALS.map((m) => (
              <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <input
            type="number"
            placeholder="density kg/m³"
            value={density}
            onChange={(e) => setDensity(e.target.value)}
            className="font-mono rounded px-2 py-1"
            style={{ ..._exportInputStyle, width: 110 }}
          />
        </div>

        {format === 'urdf' && (
          <div className="flex gap-1.5 items-center">
            <input
              type="text"
              placeholder="link name"
              value={linkName}
              onChange={(e) => setLinkName(e.target.value)}
              className="font-mono rounded px-2 py-1 flex-1"
              style={_exportInputStyle}
            />
            <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
              <input type="checkbox" checked={xacro} onChange={(e) => setXacro(e.target.checked)} />
              xacro
            </label>
          </div>
        )}

        {format === 'sdf' && (
          <>
            <div className="flex gap-1.5">
              <input
                type="text"
                placeholder="model name"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
              <input
                type="text"
                placeholder="link name"
                value={linkName}
                onChange={(e) => setLinkName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
            </div>
            <div className="flex gap-1.5 items-center">
              <input
                type="text"
                placeholder="world name (optional)"
                value={worldName}
                onChange={(e) => setWorldName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
              <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                <input type="checkbox" checked={staticFlag} onChange={(e) => setStaticFlag(e.target.checked)} />
                static
              </label>
            </div>
          </>
        )}

        {format === 'usd' && (
          <input
            type="text"
            placeholder="prim name"
            value={primName}
            onChange={(e) => setPrimName(e.target.value)}
            className="font-mono rounded px-2 py-1"
            style={_exportInputStyle}
          />
        )}
      </div>

      <Button variant="primary" size="sm" onClick={handleSubmit} disabled={pending} className="text-xs w-full">
        {pending ? 'Exporting…' : `Export ${format.toUpperCase()}`}
      </Button>

      {result && (
        <div className="flex gap-1.5 mt-2" style={{ flexWrap: 'wrap' }}>
          <a href={toDownloadHref(result.outputFile.download_url)} download style={{ textDecoration: 'none' }}>
            <FileActionBtn icon="download" label={result.outputFile.filename} />
          </a>
          <a href={toDownloadHref(result.meshFile.download_url)} download style={{ textDecoration: 'none' }}>
            <FileActionBtn icon="download" label={result.meshFile.filename} />
          </a>
        </div>
      )}
    </div>
  );
}

function NodeDetail({ node, onClose }: { node: TwinNode; onClose: () => void }) {
  const loadModel = useViewerStore((s) => s.loadModel);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const openBooleanCut = useViewerStore((s) => s.openBooleanCut);
  const [loading3d, setLoading3d] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const isCAD = node.properties.wp_type === 'cad_model';

  const handleView3D = useCallback(async () => {
    setLoading3d(true);
    try {
      const result = await getNodeModel(node.id);
      const manifest: ModelManifest = {
        parts: result.metadata.parts.map((p) => ({
          name: p.name,
          meshName: p.meshName ?? p.name,
          children: (p.children ?? []) as ModelManifest['parts'],
          boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
        })),
        meshToNodeMap: {},
        materials: result.metadata.materials ?? [],
        stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
      };
      const glbUrl = resolveGatewayHref(result.glb_url);
      loadModel(glbUrl, manifest);
      setViewMode('3d');
    } catch (err) {
      console.error('Failed to load 3D model:', err);
    } finally {
      setLoading3d(false);
    }
  }, [node.id, loadModel, setViewMode]);

  // Boolean-cut targets whichever node is open in the 3D panel (MET-612) —
  // ensure this node is actually loaded there first, then enter picking mode.
  const handleBooleanCut = useCallback(async () => {
    await handleView3D();
    openBooleanCut(node.id);
  }, [handleView3D, openBooleanCut, node.id]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 flex-shrink-0"
        style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
      >
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.orange }}>
            {iconForNode(node)}
          </span>
          <span className="font-mono text-xs truncate" style={{ color: KC.onSurface, maxWidth: 180 }}>
            {node.name}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 4 }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurface; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurfaceVariant; }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Status + meta */}
        <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
          <StatusBadge status={node.status} />
          <div className="font-mono mt-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
            {node.domain} · {node.type} · {formatRelativeTime(node.updatedAt)}
          </div>
        </div>

        {/* View 3D / Boolean cut (MET-612) */}
        {isCAD && (
          <div className="px-3 py-2 flex-shrink-0 flex gap-1.5" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <Button variant="primary" size="sm" onClick={handleView3D} disabled={loading3d} className="text-xs flex-1">
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>view_in_ar</span>
              {loading3d ? 'Loading…' : 'View 3D Model'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleBooleanCut}
              disabled={loading3d}
              className="text-xs"
              title="Boolean cut against another CAD node"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle' }}>content_cut</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setExportOpen((v) => !v)}
              className="text-xs"
              title="Export for robotics sim (URDF/SDF/USD)"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle' }}>precision_manufacturing</span>
            </Button>
          </div>
        )}

        {/* Export for robotics sim (MET-720) */}
        {isCAD && exportOpen && (
          <ExportForSimSection node={node} onClose={() => setExportOpen(false)} />
        )}

        {/* View a saved robot description directly -- no export form, no
         * manual part/joint re-entry (MET-740 follow-up). */}
        <RobotDescriptionViewSection node={node} />

        {/* File: worktype + path + download / open / preview (MET-483) */}
        <WorkProductFileSection node={node} />

        {/* Properties */}
        {Object.keys(node.properties).length > 0 && (
          <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Properties
            </div>
            <table className="w-full" style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {Object.entries(node.properties).map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}>
                    <td className="py-1 pr-3 font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, width: '40%' }}>{k}</td>
                    <td className="py-1 font-mono" style={{ fontSize: 11, color: KC.onSurface }}>{String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Revision history (GET /v1/twin/nodes/{id}/versions) */}
        <NodeHistorySection nodeId={node.id} />

        {/* Pending design-change proposals for this node (gated apply, MET-548) */}
        <div className="px-3 py-2 flex-shrink-0">
          <NodeProposals nodeId={node.id} onApplied={isCAD ? handleView3D : undefined} />
        </div>
      </div>
    </div>
  );
}

// ── RobotDescriptionViewSection ─────────────────────────────────────────────────
/**
 * MET-740 follow-up: "just look at a robot I already built" was a real
 * complaint -- the only path to a URDF preview went through the Assembly
 * export panel's full form (pick every part from a dropdown, re-enter
 * every joint), even for a robot that was already fully specified and
 * saved. This is a pure READ action instead: one click, no form, backed
 * directly by GET /nodes/{id}/file + GET /nodes/{id}/files/{filename}
 * (the same persisted node data the Assembly panel's "Load existing
 * robot description" dropdown reads, just skipping the form entirely).
 *
 * MET-747: previously opened a second, independent floating Canvas
 * (UrdfPreviewPanel, portal'd to escape MET-746's backdrop-filter clipping)
 * instead of the main viewer already used for cad_model nodes -- two
 * separate Three.js scenes/OrbitControls/render loops for what is, from
 * the user's perspective, "view this thing in 3D". Now this button just
 * loads the robot description into the SAME main viewer/Canvas (R3FViewer),
 * the same way "View 3D Model" loads a GLB there -- one viewer, dispatched
 * on the selected node's wp_type instead of a bespoke popup per node type.
 */
function RobotDescriptionViewSection({ node }: { node: TwinNode }) {
  const loadRobotDescription = useViewerStore((s) => s.loadRobotDescription);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  if (node.properties.wp_type !== 'robot_description') return null;

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <Button
        variant="primary"
        size="sm"
        onClick={() => {
          loadRobotDescription(node.id);
          setViewMode('3d');
        }}
        className="text-xs w-full"
      >
        <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>smart_toy</span>
        View Robot
      </Button>
    </div>
  );
}

// ── NodeHistorySection ─────────────────────────────────────────────────────────
/**
 * Revision history for the selected node. `useNodeVersionHistory` and its
 * underlying endpoint already existed but had no UI consumer anywhere in
 * the dashboard — this is that missing consumer.
 */
function NodeHistorySection({ nodeId }: { nodeId: string }) {
  const { data: revisions, isLoading } = useNodeVersionHistory(nodeId);
  const [expanded, setExpanded] = useState(false);

  if (isLoading || !revisions || revisions.length === 0) return null;

  const sorted = [...revisions].sort((a, b) => b.revision - a.revision);
  const visible = expanded ? sorted : sorted.slice(0, 3);

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          History · {revisions.length}
        </div>
        {sorted.length > 3 && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: KC.onSurfaceVariant, fontSize: 10, fontFamily: 'inherit' }}
          >
            {expanded ? 'Show less' : `Show all ${sorted.length}`}
          </button>
        )}
      </div>
      <div className="space-y-1.5">
        {visible.map((rev) => (
          <div key={rev.revision} className="flex items-start gap-2" style={{ fontSize: 11 }}>
            <span
              className="font-mono rounded px-1 flex-shrink-0"
              style={{ background: KC.surfaceHigh, color: KC.onSurfaceVariant, fontSize: 10 }}
            >
              v{rev.revision}
            </span>
            <div className="min-w-0">
              <div style={{ color: KC.onSurface }}>{rev.change_description}</div>
              <div className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                {formatRelativeTime(rev.created_at)} · {rev.content_hash.slice(0, 8)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── TwinViewerPage ────────────────────────────────────────────────────────────
type ConversionPhase = 'idle' | 'uploading' | 'converting' | 'loading';
type TwinTab = 'graph' | 'model' | 'sim' | 'asm';
type InspectorTab = 'overview' | 'constraints' | 'history';
type NodeScope = 'all' | 'attention' | string;

const TWIN_TABS: TwinTab[] = ['graph', 'model', 'sim', 'asm'];
const TAB_LABELS: Record<TwinTab, string> = { graph: 'Graph', model: 'Model', sim: 'Sim', asm: 'Assembly' };
const INSPECTOR_TABS: InspectorTab[] = ['overview', 'constraints', 'history'];

/** Status words that put a node in the "need attention" bucket. */
function needsAttention(node: TwinNode): boolean {
  return /fail|invalid|violation|stale|changed|warning/i.test(node.status);
}

export function TwinViewerPage() {
  const sampleMode = isSampleMode();

  // ── state ──
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [assemblyExportOpen, setAssemblyExportOpen] = useState(false);
  const [conversionPhase, setConversionPhase] = useState<ConversionPhase>('idle');
  const [quality, setQuality] = useState('standard');
  const [agentOpen, setAgentOpen] = useState(true);
  const [chatMax, setChatMax] = useState(false);
  const [chatMin, setChatMin] = useState(!sampleMode);
  const [chatDock, setChatDock] = useState<'overlay' | 'side'>('overlay');
  const [tab, setTab] = useState<TwinTab>('graph');
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('overview');
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState<NodeScope>('all');
  const [timelineOpen, setTimelineOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const railMin = useLayoutStore((s) => s.sidebarCollapsed);
  const toggleRail = useLayoutStore((s) => s.toggleSidebar);

  // Capture the live WebGL canvas as a PNG download (Screenshot button).
  function handleScreenshot() {
    const canvas = document.querySelector<HTMLCanvasElement>('.twin-canvas canvas');
    if (!canvas) return;
    const url = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url;
    a.download = `twin-${Date.now()}.png`;
    a.click();
  }
  // Track which node's model is loaded so the auto-loader (MET-505) doesn't refetch.
  const [loadedModelNodeId, setLoadedModelNodeId] = useState<string | null>(null);
  // Same tracking for the robot-description auto-loader (MET-747).
  const [loadedRobotNodeId, setLoadedRobotNodeId] = useState<string | null>(null);
  // MET-683: distinguish "nothing loaded yet" from "we tried and the backend
  // rejected it" -- a failed conversion must not silently fall back to the
  // generic upload placeholder.
  const [modelLoadError, setModelLoadError] = useState<string | null>(null);
  // Deep-link: /twin?node=<id> preselects a node (MET-514).
  const [searchParams] = useSearchParams();

  // ── project scope ──
  // The single global active-project selection (Topbar switcher). Sample mode
  // pins the illustrative Drone FC workspace.
  const { activeProjectId: selectedProjectId, activeProject } = useActiveProject();
  const activeProjectId = sampleMode ? SAMPLE_PROJECT_ID : selectedProjectId;
  const projectName = sampleMode ? SAMPLE_PROJECT_NAME : activeProject?.name;

  // ── data ──
  const {
    data: nodes,
    isLoading,
    isError,
    isFetching,
    dataUpdatedAt,
  } = useTwinNodes(activeProjectId ?? undefined);
  const { data: selectedNode } = useTwinNode(selectedId ?? undefined);
  const { data: relationships = [] } = useTwinRelationships(activeProjectId ?? undefined);
  const items = nodes ?? [];

  // ── viewer store ──
  const viewMode = useViewerStore((s) => s.viewMode);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const manifest = useViewerStore((s) => s.manifest);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const selectPart = useViewerStore((s) => s.selectPart);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const loadModel = useViewerStore((s) => s.loadModel);
  const clearModel = useViewerStore((s) => s.clearModel);
  const loadRobotDescription = useViewerStore((s) => s.loadRobotDescription);
  const robotDescription = useViewerStore((s) => s.robotDescription);
  const robotPhysicsEnabled = useViewerStore((s) => s.robotPhysicsEnabled);
  const setRobotPhysicsEnabled = useViewerStore((s) => s.setRobotPhysicsEnabled);

  const uploadMutation = useUploadAndConvert();

  // MET-514: sync the selected node with the ?node= deep link. Symmetric --
  // clears the selection when the param disappears too (MET-686).
  useEffect(() => {
    setSelectedId(searchParams.get('node'));
  }, [searchParams]);

  // MET-674: clear the selected node (and its cached model) when the active
  // project changes. Skips the null -> X transition (MET-686) so a cold
  // session's auto-selected project doesn't wipe a deep-linked node.
  const prevProjectIdRef = useRef(activeProjectId);
  useEffect(() => {
    if (prevProjectIdRef.current !== null && prevProjectIdRef.current !== activeProjectId) {
      setSelectedId(null);
      setLoadedModelNodeId(null);
      setLoadedRobotNodeId(null);
    }
    prevProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  // MET-683: a stale error from a previously-selected node must not linger.
  useEffect(() => {
    setModelLoadError(null);
  }, [selectedNode?.id]);

  // MET-505: in MODEL view, auto-load the selected node's geometry.
  useEffect(() => {
    if (viewMode !== '3d') return;
    const n = selectedNode;
    if (!n || n.properties.wp_type !== 'cad_model') return;
    // MET-747: also reload if a robot description's mutual-exclusion clear
    // wiped glbUrl since this node was last loaded.
    if (loadedModelNodeId === n.id && glbUrl) return;
    // MET-683: clear any PREVIOUS node's geometry before attempting this
    // node's load so a failure never leaves the prior model on screen.
    clearModel();
    let cancelled = false;
    (async () => {
      try {
        const result = await getNodeModel(n.id);
        if (cancelled) return;
        const m: ModelManifest = {
          parts: result.metadata.parts.map((p) => ({
            name: p.name,
            meshName: p.meshName ?? p.name,
            children: (p.children ?? []) as ModelManifest['parts'],
            boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
          })),
          meshToNodeMap: {},
          materials: result.metadata.materials ?? [],
          stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
        };
        const url = resolveGatewayHref(result.glb_url);
        loadModel(url, m);
        setLoadedModelNodeId(n.id);
        setModelLoadError(null);
      } catch (err) {
        if (!cancelled) {
          console.error('Failed to auto-load 3D model:', err);
          setModelLoadError(getModelLoadErrorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [viewMode, selectedNode, loadedModelNodeId, glbUrl, loadModel, clearModel]);

  // MET-747: same auto-load pattern for robot_description nodes. Guards on a
  // primitive boolean, not the robotDescription object (a fresh object per
  // load would loop forever).
  const hasRobotLoaded = Boolean(robotDescription);
  useEffect(() => {
    if (viewMode !== '3d') return;
    const n = selectedNode;
    if (!n || n.properties.wp_type !== 'robot_description') return;
    if (loadedRobotNodeId === n.id && hasRobotLoaded) return;
    loadRobotDescription(n.id);
    setLoadedRobotNodeId(n.id);
  }, [viewMode, selectedNode, loadedRobotNodeId, hasRobotLoaded, loadRobotDescription]);

  useEffect(() => {
    if (!uploadMutation.isPending) {
      if (uploadMutation.isSuccess && conversionPhase === 'converting') {
        setConversionPhase('loading');
        const t = setTimeout(() => setConversionPhase('idle'), 800);
        return () => clearTimeout(t);
      }
      if (!uploadMutation.isSuccess) setConversionPhase('idle');
    }
  }, [uploadMutation.isPending, uploadMutation.isSuccess, conversionPhase]);

  const handleUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        setConversionPhase('uploading');
        const t = setTimeout(() => setConversionPhase('converting'), 1200);
        uploadMutation.mutate({ file, quality }, { onSettled: () => clearTimeout(t) });
      }
    },
    [uploadMutation, quality],
  );

  const handlePartClick = useCallback(
    (part: PartInfo) => {
      selectPart(part.meshName);
    },
    [selectPart],
  );

  // Something else (e.g. "View 3D Model") switched the viewer to 3D: follow it.
  useEffect(() => {
    if (viewMode === '3d') setTab((t) => (t === 'graph' ? 'model' : t));
  }, [viewMode]);

  const switchTab = (next: TwinTab) => {
    setTab(next);
    setViewMode(next === 'graph' || next === 'asm' ? 'graph' : '3d');
  };

  const selectNode = (id: string | null) => {
    setSelectedId(id);
    setInspectorOpen(true);
  };

  // ── derived ──
  const query = search.toLowerCase();
  const visible = items.filter(
    (n) =>
      (scope === 'all' || (scope === 'attention' && needsAttention(n)) || n.domain === scope) &&
      `${n.name} ${n.domain} ${n.id}`.toLowerCase().includes(query),
  );
  const visibleIds = new Set(visible.map((n) => n.id));
  const domains = [...new Set(items.map((n) => n.domain))];
  const linked = new Set(relationships.flatMap((r) => [r.sourceId, r.targetId]));
  const linkedConstraints = items.filter(
    (n) =>
      n.type === 'constraint' &&
      (!selectedId ||
        n.id === selectedId ||
        relationships.some(
          (r) => (r.sourceId === selectedId && r.targetId === n.id) || (r.targetId === selectedId && r.sourceId === n.id),
        )),
  );
  // Only inspect a node that belongs to the current node list (MET-674).
  const node = selectedNode && items.some((n) => n.id === selectedNode.id) ? selectedNode : undefined;
  const recent = [...items]
    .filter((n) => n.updatedAt)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 12);
  const robotNodes = items.filter((n) => n.properties.wp_type === 'robot_description');
  const is3d = tab === 'model' || tab === 'sim';

  const syncLabel = sampleMode
    ? 'Sample data'
    : isError
      ? 'Gateway disconnected'
      : isFetching
        ? 'Syncing…'
        : dataUpdatedAt
          ? 'Synced'
          : 'Awaiting data';

  return (
    <div
      className="tw-workspace"
      data-inspector={inspectorOpen ? 'on' : 'off'}
      data-rail={railMin ? 'min' : 'on'}
      data-chat={chatDock}
    >
      <header className="tw-top">
        <button
          className="tw-icon"
          onClick={toggleRail}
          title={railMin ? 'Expand navigation and explorer' : 'Minimise navigation and explorer'}
          aria-expanded={!railMin}
        >
          <PanelLeftClose size={18} />
        </button>
        <div className="tw-brand">
          <Network size={18} />
          <h1>Digital Twin</h1>
          <span>{projectName ?? 'No project'}</span>
        </div>
        <label className="tw-search">
          <Search size={15} />
          <input
            type="search"
            placeholder="Search the twin"
            aria-label="Search the twin"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="tw-segment" role="group" aria-label="View mode">
          {TWIN_TABS.map((t) => (
            <button key={t} aria-pressed={tab === t} onClick={() => switchTab(t)}>
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
        {is3d && glbUrl && (
          <button className="tw-icon" title="Screenshot" onClick={handleScreenshot}>
            <Camera size={18} />
          </button>
        )}
        <button className="tw-icon" title="Import work product" onClick={() => setImportOpen(true)}>
          <Upload size={18} />
        </button>
        <button
          className="tw-icon"
          title="Toggle inspector"
          aria-pressed={inspectorOpen}
          onClick={() => setInspectorOpen((v) => !v)}
        >
          <PanelRightClose size={18} />
        </button>
      </header>

      <section className="tw-attention" aria-label="Needs attention">
        {sampleMode && <strong className="sample-badge">Sample data · resets on refresh</strong>}
        <button
          aria-pressed={scope === 'attention'}
          onClick={() => setScope(scope === 'attention' ? 'all' : 'attention')}
        >
          <span className="tw-warning">{items.filter(needsAttention).length}</span> need attention
        </button>
        <button
          onClick={() => {
            setSearch('');
            setScope('all');
          }}
        >
          <b>{items.length}</b> work products & nodes
        </button>
        <span>
          <b>{items.filter((n) => !linked.has(n.id)).length}</b> without relationships
        </span>
        <Link to={`/runs/new${activeProjectId ? '?project=' + encodeURIComponent(activeProjectId) : ''}`}>
          Start design run ↗
        </Link>
      </section>

      <section className="tw-view" aria-label="Twin model and graph">
        <div className="tw-render-area">
          {tab === 'graph' ? (
            isLoading ? (
              <div className="tw-empty" role="status">
                Loading twin graph…
              </div>
            ) : isError ? (
              <div className="tw-empty" role="alert">
                <Network size={30} />
                <h2>Twin data unavailable</h2>
                <p>Connect your gateway to load this project.</p>
                <Link to="/settings">Connection settings ↗</Link>
              </div>
            ) : items.length ? (
              <TwinGraphCanvas
                nodes={visible}
                relationships={relationships.filter((r) => visibleIds.has(r.sourceId) && visibleIds.has(r.targetId))}
                selectedId={selectedId}
                onSelectNode={selectNode}
              />
            ) : (
              <div className="tw-empty">
                <Network size={32} />
                <h2>Empty twin</h2>
                <p>Import a work product or start a design run.</p>
                <button onClick={() => setImportOpen(true)}>Import work product</button>
              </div>
            )
          ) : tab === 'asm' ? (
            <div className="tw-assembly">
              <div className="tw-assembly-heading">
                <Layers size={20} />
                <h2>Assembly</h2>
                <button onClick={() => setAssemblyExportOpen((v) => !v)}>Configure export</button>
              </div>
              {node?.assembly ? (
                <>
                  <h3>{node.name}</h3>
                  <div className="tw-assembly-tree">
                    {node.assembly.parts.map((part) => (
                      <button key={part.link_name} onClick={() => selectNode(part.node_id)}>
                        <Box size={16} />
                        {part.link_name}
                        <small>{part.material || 'Material unspecified'}</small>
                      </button>
                    ))}
                    {node.assembly.joints.map((joint) => (
                      <p key={joint.name}>
                        {joint.base} → {joint.follower}
                        <small>
                          {joint.name} · {joint.type}
                        </small>
                      </p>
                    ))}
                  </div>
                  <details>
                    <summary>Assembly source</summary>
                    <pre>{JSON.stringify(node.assembly, null, 2)}</pre>
                  </details>
                </>
              ) : (
                <div className="tw-empty">
                  <Layers size={32} />
                  <h2>Build the assembly</h2>
                  <p>
                    Select a robot description to inspect its links and joints, or configure an export from your work
                    products.
                  </p>
                  <button onClick={() => setAssemblyExportOpen(true)}>Configure assembly</button>
                </div>
              )}
            </div>
          ) : (
            <>
              <R3FViewer onPartClick={handlePartClick} onBooleanCutComplete={selectNode} />
              {!glbUrl && modelLoadError && (
                <div className="tw-model-error" role="alert">
                  <strong>Model failed to load</strong> {modelLoadError}
                </div>
              )}
              {tab === 'model' && glbUrl && (
                <div className="tw-view-controls">
                  <ExplodedViewControls />
                </div>
              )}
              {tab === 'sim' && (
                <div className="tw-sim-controls">
                  <Activity size={16} />
                  <strong>Robotics physics</strong>
                  <button
                    disabled={!robotDescription}
                    aria-pressed={robotPhysicsEnabled}
                    onClick={() => setRobotPhysicsEnabled(!robotPhysicsEnabled)}
                  >
                    {robotPhysicsEnabled ? 'Stop' : 'Run'}
                  </button>
                  <span>
                    {robotDescription
                      ? 'Gravity · joint constraints · simplified colliders'
                      : 'Select a robot-description node'}
                  </span>
                </div>
              )}
              {tab === 'sim' && !robotDescription && (
                <div className="tw-sim-prompt">
                  <h2>Choose a robot to simulate</h2>
                  <p>Load its geometry and joint configuration into the same workspace.</p>
                  {robotNodes.map((n) => (
                    <button key={n.id} onClick={() => selectNode(n.id)}>
                      {n.name} ↗
                    </button>
                  ))}
                  <Link to="/runs">View recorded simulation runs ↗</Link>
                </div>
              )}
            </>
          )}
        </div>

        <aside className="tw-explorer" aria-label="Explorer">
          {railMin ? (
            <div className="tw-rail-min">
              <button
                title="All work products"
                onClick={() => {
                  setScope('all');
                  toggleRail();
                }}
              >
                <Layers size={18} />
                <small>{items.length}</small>
              </button>
              {domains.map((d) => (
                <button
                  key={d}
                  title={d}
                  onClick={() => {
                    setScope(d);
                    toggleRail();
                  }}
                >
                  <span>{d.slice(0, 2).toUpperCase()}</span>
                  <small>{items.filter((n) => n.domain === d).length}</small>
                </button>
              ))}
            </div>
          ) : (
            <>
              <header>
                <h2>Explorer</h2>
                <span>{visible.length} nodes</span>
                <button className="tw-icon" aria-label="Collapse nodes panel" onClick={toggleRail}>
                  <PanelLeftClose size={16} />
                </button>
              </header>
              <div className="tw-scope">
                <button aria-pressed={scope === 'all'} onClick={() => setScope('all')}>
                  All
                </button>
                <button aria-pressed={scope === 'attention'} onClick={() => setScope('attention')}>
                  Needs attention
                </button>
              </div>
              <div className="tw-node-list">
                {!visible.length && <p className="tw-muted">{search ? 'No matching work products' : 'No work products'}</p>}
                {domains.map((d) => (
                  <section key={d}>
                    {visible.some((n) => n.domain === d) && <h3>{d}</h3>}
                    {visible
                      .filter((n) => n.domain === d)
                      .map((n) => (
                        <button
                          key={n.id}
                          title={n.name}
                          aria-current={n.id === selectedId ? 'true' : undefined}
                          onClick={() => selectNode(n.id)}
                        >
                          <span className={`tw-node-state ${needsAttention(n) ? 'needs-attention' : ''}`} />
                          <span className="material-symbols-outlined">{iconForNode(n)}</span>
                          <span>{n.name}</span>
                        </button>
                      ))}
                  </section>
                ))}
                {is3d && manifest && (
                  <section className="tw-component-tree">
                    <ComponentTree />
                  </section>
                )}
              </div>
            </>
          )}
        </aside>

        {importOpen && (
          <section className="tw-popover" aria-label="Import work product">
            <header>
              <h2>Import work product</h2>
              <button className="tw-icon" aria-label="Close import" onClick={() => setImportOpen(false)}>
                <X size={16} />
              </button>
            </header>
            <p>Convert CAD geometry for the twin viewer.</p>
            <label>
              Geometry quality
              <select value={quality} onChange={(e) => setQuality(e.target.value)}>
                <option value="preview">Preview</option>
                <option value="standard">Standard</option>
                <option value="fine">Fine</option>
              </select>
            </label>
            <button className="tw-upload" disabled={uploadMutation.isPending} onClick={() => fileInputRef.current?.click()}>
              <Upload size={24} />
              {conversionPhase === 'idle' ? 'Choose STEP or IGES file' : `${conversionPhase}…`}
            </button>
            <input ref={fileInputRef} type="file" accept=".step,.stp,.iges,.igs" hidden onChange={handleUpload} />
            {uploadMutation.isError && <p role="alert">Import failed. Check the gateway connection and file.</p>}
          </section>
        )}

        {assemblyExportOpen && (
          <div className="tw-export-panel">
            <AssemblyExportPanel
              items={items}
              onClose={() => setAssemblyExportOpen(false)}
              activeProjectId={activeProjectId}
            />
          </div>
        )}

        {timelineOpen && (
          <section className="tw-timeline">
            <header>
              <h2>Latest work product updates</h2>
              <button className="tw-icon" aria-label="Close timeline" onClick={() => setTimelineOpen(false)}>
                <X size={16} />
              </button>
            </header>
            <div>
              {recent.length ? (
                recent.map((n) => (
                  <button key={n.id} onClick={() => selectNode(n.id)}>
                    <time>{formatRelativeTime(n.updatedAt)}</time>
                    <strong>{n.name}</strong>
                    <span>{n.status}</span>
                  </button>
                ))
              ) : (
                <p>No recorded updates for this project.</p>
              )}
            </div>
            <Link to="/sessions">Full session history ↗</Link>
          </section>
        )}
      </section>

      <div className="tw-chat-position" data-size={chatMax ? 'max' : chatMin ? 'min' : 'normal'} hidden={!agentOpen}>
        <TwinAgentChat
          key={activeProjectId ?? 'unscoped'}
          projectId={activeProjectId}
          projectName={projectName}
          node={node}
          onApplied={() => {
            setLoadedModelNodeId(null);
            clearModel();
          }}
          onEngage={() => setChatMin(false)}
          headerActions={
            <>
              <button
                className="sc-icon"
                title={chatMin ? 'Open conversation' : 'Minimise conversation'}
                onClick={() => {
                  setChatMin((v) => !v);
                  setChatMax(false);
                }}
              >
                <ChevronDown size={16} />
              </button>
              <button
                className="sc-icon"
                title={chatMax ? 'Restore conversation' : 'Expand conversation'}
                onClick={() => {
                  setChatMax((v) => !v);
                  setChatMin(false);
                }}
              >
                {chatMax ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
              <button
                className="sc-icon"
                title={chatDock === 'side' ? 'Float conversation' : 'Dock conversation to side'}
                onClick={() => {
                  setChatDock((d) => (d === 'side' ? 'overlay' : 'side'));
                  setChatMin(false);
                }}
              >
                <Columns2 size={16} />
              </button>
            </>
          }
        />
      </div>

      {inspectorOpen && (
        <aside className="tw-inspector" aria-label="Node inspector">
          <header>
            <div>
              <h2>{node?.name || selectedMeshName || 'Inspector'}</h2>
              <p>{node ? `${node.domain} · ${node.type}` : 'Select an object to inspect'}</p>
            </div>
            <button className="tw-icon" aria-label="Close inspector" onClick={() => setInspectorOpen(false)}>
              <X size={16} />
            </button>
          </header>
          <div className="tw-inspector-tabs" role="group" aria-label="Inspector view">
            {INSPECTOR_TABS.map((t) => (
              <button key={t} aria-pressed={inspectorTab === t} onClick={() => setInspectorTab(t)}>
                {t}
              </button>
            ))}
          </div>
          <div className="tw-inspector-content">
            {node ? (
              inspectorTab === 'overview' ? (
                <NodeDetail node={node} onClose={() => setSelectedId(null)} />
              ) : inspectorTab === 'history' ? (
                <NodeHistorySection nodeId={node.id} />
              ) : (
                <div className="tw-constraints">
                  {linkedConstraints.length ? (
                    linkedConstraints.map((c) => (
                      <button key={c.id} onClick={() => selectNode(c.id)}>
                        <strong>{c.name}</strong>
                        <span>{c.status}</span>
                      </button>
                    ))
                  ) : (
                    <p>No linked constraints recorded.</p>
                  )}
                </div>
              )
            ) : selectedMeshName && manifest ? (
              <BomAnnotationPanel />
            ) : (
              <div className="tw-empty">
                <Box size={28} />
                <p>Inspect geometry, properties and the evidence behind a design.</p>
              </div>
            )}
          </div>
        </aside>
      )}

      <footer className="tw-status">
        <span>{node?.name || selectedMeshName || 'No selection'}</span>
        <button onClick={() => setTimelineOpen((v) => !v)} aria-expanded={timelineOpen}>
          <Clock3 size={14} />
          Timeline
        </button>
        <button onClick={() => setAgentOpen((v) => !v)} aria-pressed={agentOpen}>
          <MessageSquare size={14} />
          Agent
        </button>
        <span className="tw-sync">
          {syncLabel}
          {dataUpdatedAt > 0 && !isError && <small>{formatRelativeTime(new Date(dataUpdatedAt).toISOString())}</small>}
        </span>
      </footer>
    </div>
  );
}
