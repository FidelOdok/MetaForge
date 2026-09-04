import { useRef, useState } from 'react';
import { Button } from '../ui/Button';
import { useToast } from '../ui/Toast';
import {
  useExportUrdfAssembly,
  useExportSdfAssembly,
  useExportUsdAssembly,
  useGenerateRos2Launch,
  useSessionSummary,
  useSessionJoints,
} from '../../hooks/use-cad-export';
import { toDownloadHref, type ExportFile, type JointType } from '../../api/endpoints/cad-export';
import type { TwinNode } from '../../types/twin';

// Kinetic Console palette — same rationale as BooleanCutPanel.tsx (this file
// predates the token set living anywhere shared/importable).
const KC_SURFACE = 'rgba(30,31,38,0.92)';
const KC_BORDER = 'rgba(65,72,90,0.3)';
const KC_BORDER_MID = 'rgba(65,72,90,0.45)';
const KC_ON_SURFACE = '#e2e2eb';
const KC_ON_SURFACE_VARIANT = '#9a9aaa';
const KC_ORANGE = '#e67e22';
const KC_ORANGE_FAINT = 'rgba(230,126,34,0.15)';
const KC_ORANGE_BORDER = 'rgba(230,126,34,0.45)';

const inputStyle: React.CSSProperties = {
  fontSize: 11,
  background: '#1e1f26',
  border: `1px solid ${KC_BORDER}`,
  color: KC_ON_SURFACE,
  borderRadius: 4,
  padding: '4px 6px',
};

const JOINT_TYPES: JointType[] = ['fixed', 'slider', 'revolute', 'cylindrical', 'ball'];
const FORMATS = ['urdf', 'sdf', 'usd'] as const;
type Format = (typeof FORMATS)[number];

function slugifyLinkName(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return slug || 'part';
}

interface PartRow {
  key: number;
  nodeId: string;
  linkName: string;
  material: string;
  density: string;
}

interface JointRow {
  key: number;
  name: string;
  type: JointType;
  base: string;
  follower: string;
  axis: [string, string, string];
  anchor: [string, string, string];
  limitsLower: string;
  limitsUpper: string;
}

function getErrorDetail(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const response = (err as { response?: { data?: { detail?: string } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return fallback;
}

interface AssemblyExportPanelProps {
  /** All currently-loaded Twin nodes — filtered to CAD parts for the picker. */
  items: TwinNode[];
  onClose: () => void;
}

/**
 * MET-721: export a multi-part assembly (with joints) to URDF/SDF/USD.
 *
 * Joints are never persisted anywhere durable — they only exist inside a
 * LIVE FreeCAD authoring session (default 30 min idle TTL). There is no way
 * to look one up by Twin/assembly node id, so this panel's "reuse joints
 * from chat" convenience requires the user to already have a session_id
 * (e.g. one echoed by a recent chat turn) — it is not auto-discovered.
 * Manually adding/editing parts and joints below always works regardless.
 */
export function AssemblyExportPanel({ items, onClose }: AssemblyExportPanelProps) {
  const toast = useToast();
  const nextKey = useRef(0);
  const cadNodes = items.filter((n) => n.properties.wp_type === 'cad_model');

  const [parts, setParts] = useState<PartRow[]>([]);
  const [joints, setJoints] = useState<JointRow[]>([]);
  const [format, setFormat] = useState<Format>('urdf');
  const [robotName, setRobotName] = useState('robot');
  const [modelName, setModelName] = useState('model');
  const [xacro, setXacro] = useState(false);
  const [worldName, setWorldName] = useState('');
  const [staticFlag, setStaticFlag] = useState(false);
  const [result, setResult] = useState<{ outputFile: ExportFile; meshFiles: ExportFile[] } | null>(null);
  const [launchFile, setLaunchFile] = useState<ExportFile | null>(null);

  const [sessionIdInput, setSessionIdInput] = useState('');
  const [fetchedSessionId, setFetchedSessionId] = useState('');
  const sessionSummary = useSessionSummary(fetchedSessionId, fetchedSessionId.length > 0);
  const sessionJoints = useSessionJoints(fetchedSessionId, fetchedSessionId.length > 0);

  const urdfAssembly = useExportUrdfAssembly();
  const sdfAssembly = useExportSdfAssembly();
  const usdAssembly = useExportUsdAssembly();
  const ros2Launch = useGenerateRos2Launch();
  const pending = urdfAssembly.isPending || sdfAssembly.isPending || usdAssembly.isPending;

  const addPart = (nodeId: string) => {
    const node = cadNodes.find((n) => n.id === nodeId);
    if (!node) return;
    setParts((prev) => [
      ...prev,
      { key: nextKey.current++, nodeId, linkName: slugifyLinkName(node.name), material: '', density: '' },
    ]);
  };

  const addJoint = () => {
    setJoints((prev) => [
      ...prev,
      {
        key: nextKey.current++,
        name: '',
        type: 'revolute',
        base: '',
        follower: '',
        axis: ['0', '0', '1'],
        anchor: ['0', '0', '0'],
        limitsLower: '',
        limitsUpper: '',
      },
    ]);
  };

  const importPartsFromSession = () => {
    if (!sessionSummary.data) return;
    // Session objects have no Twin node_id (they're FreeCAD-internal) — this
    // only pre-fills the link_name so it matches what the imported joints'
    // base/follower reference; the user still has to pick each part's Twin
    // node from the dropdown below.
    const imported: PartRow[] = sessionSummary.data.objects
      .filter((o) => o.kind !== 'joint')
      .map((o) => ({ key: nextKey.current++, nodeId: '', linkName: o.name, material: '', density: '' }));
    setParts((prev) => [...prev, ...imported]);
    toast.info(`Imported ${imported.length} part slot(s) — pick each one's Twin node below`);
  };

  const importJointsFromSession = () => {
    if (!sessionJoints.data) return;
    const imported: JointRow[] = sessionJoints.data.joints.map((j) => ({
      key: nextKey.current++,
      name: j.name,
      type: j.type,
      base: j.base,
      follower: j.follower,
      axis: [String(j.axis[0]), String(j.axis[1]), String(j.axis[2])],
      anchor: [String(j.anchor[0]), String(j.anchor[1]), String(j.anchor[2])],
      limitsLower: j.limits?.lower !== undefined ? String(j.limits.lower) : '',
      limitsUpper: j.limits?.upper !== undefined ? String(j.limits.upper) : '',
    }));
    setJoints((prev) => [...prev, ...imported]);
    toast.success(`Imported ${imported.length} joint(s) from session`);
  };

  const handleSubmit = () => {
    if (parts.length === 0) {
      toast.error('Add at least one part');
      return;
    }
    if (parts.some((p) => !p.nodeId || !p.linkName.trim())) {
      toast.error('Every part needs both a Twin node and a link name');
      return;
    }

    setResult(null);
    setLaunchFile(null);

    const apiParts = parts.map((p) => ({
      node_id: p.nodeId,
      link_name: p.linkName.trim(),
      material: p.material || undefined,
      density_kg_m3: p.density.trim() ? Number(p.density) : undefined,
    }));
    const apiJoints = joints.map((j) => ({
      name: j.name.trim() || `${j.base}-${j.follower}`,
      type: j.type,
      base: j.base,
      follower: j.follower,
      axis: [Number(j.axis[0]) || 0, Number(j.axis[1]) || 0, Number(j.axis[2]) || 0] as [number, number, number],
      anchor: [Number(j.anchor[0]) || 0, Number(j.anchor[1]) || 0, Number(j.anchor[2]) || 0] as [number, number, number],
      limits:
        j.limitsLower.trim() && j.limitsUpper.trim()
          ? { lower: Number(j.limitsLower), upper: Number(j.limitsUpper) }
          : undefined,
    }));

    const onSuccess = (data: { output_file: ExportFile; mesh_files: ExportFile[] }) => {
      setResult({ outputFile: data.output_file, meshFiles: data.mesh_files });
      toast.success(`Exported ${data.output_file.filename}`);
    };
    const onError = (err: unknown) => {
      toast.error(getErrorDetail(err, `${format.toUpperCase()} assembly export failed`));
    };

    if (format === 'urdf') {
      urdfAssembly.mutate(
        { parts: apiParts, joints: apiJoints, robot_name: robotName || undefined, xacro },
        { onSuccess, onError },
      );
    } else if (format === 'sdf') {
      sdfAssembly.mutate(
        {
          parts: apiParts,
          joints: apiJoints,
          model_name: modelName || undefined,
          static: staticFlag,
          world_name: worldName || undefined,
        },
        { onSuccess, onError },
      );
    } else {
      usdAssembly.mutate(
        { parts: apiParts, joints: apiJoints, robot_name: robotName || undefined },
        { onSuccess, onError },
      );
    }
  };

  const handleGenerateLaunch = () => {
    if (!result) return;
    ros2Launch.mutate(
      { robot_name: robotName || 'robot', default_urdf_path: toDownloadHref(result.outputFile.download_url) },
      {
        onSuccess: (data) => {
          setLaunchFile(data.output_file);
          toast.success(`Generated ${data.output_file.filename}`);
        },
        onError: () => toast.error('ROS2 launch generation failed'),
      },
    );
  };

  return (
    <div
      className="rounded flex flex-col overflow-hidden"
      style={{
        background: KC_SURFACE,
        backdropFilter: 'blur(16px)',
        border: `1px solid ${KC_BORDER_MID}`,
        width: 380,
        maxHeight: 'calc(100vh - 88px)',
      }}
    >
      <div
        className="flex items-center justify-between px-3 flex-shrink-0"
        style={{ height: 36, borderBottom: `1px solid ${KC_BORDER}` }}
      >
        <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC_ON_SURFACE_VARIANT }}>
          Export assembly for sim
        </span>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC_ON_SURFACE_VARIANT, cursor: 'pointer', padding: 2 }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* Reuse joints from a live chat session */}
        <div>
          <div className="font-mono uppercase mb-1" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
            Reuse from chat session (optional)
          </div>
          <div className="flex gap-1.5 mb-1">
            <input
              type="text"
              placeholder="FreeCAD session id"
              value={sessionIdInput}
              onChange={(e) => setSessionIdInput(e.target.value)}
              className="flex-1"
              style={inputStyle}
            />
            <Button
              variant="secondary"
              size="sm"
              className="text-xs"
              disabled={!sessionIdInput.trim()}
              onClick={() => setFetchedSessionId(sessionIdInput.trim())}
            >
              Fetch
            </Button>
          </div>
          {fetchedSessionId && sessionSummary.isError && (
            <div className="font-mono" style={{ fontSize: 10, color: '#ffb4ab' }}>
              No live session found for "{fetchedSessionId}" — it may have expired (30 min idle) or
              never existed. Add parts/joints manually below instead.
            </div>
          )}
          {sessionSummary.data && (
            <div className="flex gap-1.5">
              <Button variant="secondary" size="sm" className="text-xs" onClick={importPartsFromSession}>
                Import {sessionSummary.data.object_count} part slot(s)
              </Button>
              {sessionJoints.data && sessionJoints.data.joints.length > 0 && (
                <Button variant="secondary" size="sm" className="text-xs" onClick={importJointsFromSession}>
                  Import {sessionJoints.data.joints.length} joint(s)
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Parts */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
              Parts ({parts.length})
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            {parts.map((p, i) => (
              <div key={p.key} className="flex gap-1.5 items-center">
                <select
                  value={p.nodeId}
                  onChange={(e) => {
                    const nodeId = e.target.value;
                    setParts((prev) => prev.map((row, idx) => (idx === i ? { ...row, nodeId } : row)));
                  }}
                  style={{ ...inputStyle, flex: 1.4 }}
                >
                  <option value="">Select Twin node…</option>
                  {cadNodes.map((n) => (
                    <option key={n.id} value={n.id}>{n.name}</option>
                  ))}
                </select>
                <input
                  type="text"
                  placeholder="link name"
                  value={p.linkName}
                  onChange={(e) => {
                    const linkName = e.target.value;
                    setParts((prev) => prev.map((row, idx) => (idx === i ? { ...row, linkName } : row)));
                  }}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <button
                  type="button"
                  onClick={() => setParts((prev) => prev.filter((_, idx) => idx !== i))}
                  title="Remove part"
                  style={{ background: 'transparent', border: 'none', color: KC_ON_SURFACE_VARIANT, cursor: 'pointer' }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                </button>
              </div>
            ))}
          </div>
          <select
            value=""
            onChange={(e) => { if (e.target.value) addPart(e.target.value); }}
            className="mt-1.5 w-full"
            style={inputStyle}
          >
            <option value="">+ Add part…</option>
            {cadNodes.map((n) => (
              <option key={n.id} value={n.id}>{n.name}</option>
            ))}
          </select>
        </div>

        {/* Joints */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.08em', color: KC_ON_SURFACE_VARIANT }}>
              Joints ({joints.length})
            </span>
            <Button variant="secondary" size="sm" className="text-xs" onClick={addJoint}>+ Add joint</Button>
          </div>
          <datalist id="assembly-export-link-names">
            {parts.map((p) => <option key={p.key} value={p.linkName} />)}
          </datalist>
          <div className="flex flex-col gap-2">
            {joints.map((j, i) => {
              const update = (patch: Partial<JointRow>) =>
                setJoints((prev) => prev.map((row, idx) => (idx === i ? { ...row, ...patch } : row)));
              return (
                <div key={j.key} className="rounded p-1.5" style={{ border: `1px solid ${KC_BORDER}` }}>
                  <div className="flex gap-1.5 mb-1">
                    <input
                      type="text"
                      placeholder="joint name"
                      value={j.name}
                      onChange={(e) => update({ name: e.target.value })}
                      style={{ ...inputStyle, flex: 1 }}
                    />
                    <select value={j.type} onChange={(e) => update({ type: e.target.value as JointType })} style={inputStyle}>
                      {JOINT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <button
                      type="button"
                      onClick={() => setJoints((prev) => prev.filter((_, idx) => idx !== i))}
                      title="Remove joint"
                      style={{ background: 'transparent', border: 'none', color: KC_ON_SURFACE_VARIANT, cursor: 'pointer' }}
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                    </button>
                  </div>
                  <div className="flex gap-1.5 mb-1">
                    <input
                      type="text"
                      list="assembly-export-link-names"
                      placeholder="base link"
                      value={j.base}
                      onChange={(e) => update({ base: e.target.value })}
                      style={{ ...inputStyle, flex: 1 }}
                    />
                    <input
                      type="text"
                      list="assembly-export-link-names"
                      placeholder="follower link"
                      value={j.follower}
                      onChange={(e) => update({ follower: e.target.value })}
                      style={{ ...inputStyle, flex: 1 }}
                    />
                  </div>
                  <div className="flex gap-1 items-center mb-1">
                    <span className="font-mono" style={{ fontSize: 9, color: KC_ON_SURFACE_VARIANT, width: 32 }}>axis</span>
                    {([0, 1, 2] as const).map((k) => (
                      <input
                        key={k}
                        type="number"
                        value={j.axis[k]}
                        onChange={(e) => {
                          const axis = [...j.axis] as [string, string, string];
                          axis[k] = e.target.value;
                          update({ axis });
                        }}
                        style={{ ...inputStyle, width: 50 }}
                      />
                    ))}
                    <span className="font-mono ml-2" style={{ fontSize: 9, color: KC_ON_SURFACE_VARIANT, width: 44 }}>anchor mm</span>
                    {([0, 1, 2] as const).map((k) => (
                      <input
                        key={k}
                        type="number"
                        value={j.anchor[k]}
                        onChange={(e) => {
                          const anchor = [...j.anchor] as [string, string, string];
                          anchor[k] = e.target.value;
                          update({ anchor });
                        }}
                        style={{ ...inputStyle, width: 50 }}
                      />
                    ))}
                  </div>
                  {j.type === 'slider' && (
                    <div className="flex gap-1.5 items-center">
                      <span className="font-mono" style={{ fontSize: 9, color: KC_ON_SURFACE_VARIANT }}>limits (required)</span>
                      <input
                        type="number"
                        placeholder="lower"
                        value={j.limitsLower}
                        onChange={(e) => update({ limitsLower: e.target.value })}
                        style={{ ...inputStyle, width: 60 }}
                      />
                      <input
                        type="number"
                        placeholder="upper"
                        value={j.limitsUpper}
                        onChange={(e) => update({ limitsUpper: e.target.value })}
                        style={{ ...inputStyle, width: 60 }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: 9, color: KC_ON_SURFACE_VARIANT }}>
            cylindrical joints (and ball for URDF) will be rejected by the backend with a clear error.
          </div>
        </div>

        {/* Format */}
        <div>
          <div className="flex gap-1 mb-1.5">
            {FORMATS.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFormat(f)}
                className="font-mono rounded px-2 py-1 uppercase"
                style={{
                  fontSize: 10,
                  background: format === f ? KC_ORANGE_FAINT : 'transparent',
                  border: `1px solid ${format === f ? KC_ORANGE_BORDER : KC_BORDER}`,
                  color: format === f ? KC_ORANGE : KC_ON_SURFACE_VARIANT,
                  cursor: 'pointer',
                }}
              >
                {f}
              </button>
            ))}
          </div>

          {format === 'urdf' && (
            <div className="flex gap-1.5 items-center">
              <input type="text" placeholder="robot name" value={robotName} onChange={(e) => setRobotName(e.target.value)} style={{ ...inputStyle, flex: 1 }} />
              <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC_ON_SURFACE_VARIANT }}>
                <input type="checkbox" checked={xacro} onChange={(e) => setXacro(e.target.checked)} /> xacro
              </label>
            </div>
          )}
          {format === 'sdf' && (
            <div className="flex flex-col gap-1.5">
              <input type="text" placeholder="model name" value={modelName} onChange={(e) => setModelName(e.target.value)} style={inputStyle} />
              <div className="flex gap-1.5 items-center">
                <input type="text" placeholder="world name (optional)" value={worldName} onChange={(e) => setWorldName(e.target.value)} style={{ ...inputStyle, flex: 1 }} />
                <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC_ON_SURFACE_VARIANT }}>
                  <input type="checkbox" checked={staticFlag} onChange={(e) => setStaticFlag(e.target.checked)} /> static
                </label>
              </div>
            </div>
          )}
          {format === 'usd' && (
            <input type="text" placeholder="robot name" value={robotName} onChange={(e) => setRobotName(e.target.value)} style={inputStyle} />
          )}
        </div>

        <Button variant="primary" size="sm" onClick={handleSubmit} disabled={pending} className="text-xs w-full">
          {pending ? 'Exporting…' : `Export ${format.toUpperCase()} assembly`}
        </Button>

        {result && (
          <div className="flex flex-col gap-1.5">
            <div className="flex gap-1.5" style={{ flexWrap: 'wrap' }}>
              <a href={toDownloadHref(result.outputFile.download_url)} download style={{ textDecoration: 'none' }}>
                <Button variant="secondary" size="sm" className="text-xs">
                  <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>download</span>
                  {result.outputFile.filename}
                </Button>
              </a>
              {result.meshFiles.map((m) => (
                <a key={m.filename} href={toDownloadHref(m.download_url)} download style={{ textDecoration: 'none' }}>
                  <Button variant="secondary" size="sm" className="text-xs">
                    <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>download</span>
                    {m.filename}
                  </Button>
                </a>
              ))}
            </div>
            {format === 'urdf' && !launchFile && (
              <Button variant="secondary" size="sm" className="text-xs" onClick={handleGenerateLaunch} disabled={ros2Launch.isPending}>
                {ros2Launch.isPending ? 'Generating…' : 'Generate ROS2 launch file'}
              </Button>
            )}
            {launchFile && (
              <a href={toDownloadHref(launchFile.download_url)} download style={{ textDecoration: 'none' }}>
                <Button variant="secondary" size="sm" className="text-xs">
                  <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>download</span>
                  {launchFile.filename}
                </Button>
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
