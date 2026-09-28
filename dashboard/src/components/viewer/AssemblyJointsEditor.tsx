import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { useUpdateAssemblyJoints } from '../../hooks/use-twin';
import { useToast } from '../ui/Toast';
import type { AssemblyDescription } from '../../types/twin';
import type { JointType } from '../../api/endpoints/cad-export';

type Joint = AssemblyDescription['joints'][number];

const JOINT_TYPES: JointType[] = ['fixed', 'slider', 'revolute', 'cylindrical', 'ball'];

interface AssemblyJointsEditorProps {
  nodeId: string;
  parts: AssemblyDescription['parts'];
  joints: Joint[];
}

/**
 * FORGE-271: editable mate/joint list on an already-committed assembly
 * node — the piece FORGE-245 (persisting joints onto the node at commit
 * time) didn't add: editing them afterward, with no live FreeCAD session
 * or re-export required. Base/follower are picked from the assembly's own
 * part list (the same link names the export panel's joint form already
 * uses) rather than a full 3D face-click picker — a real, working
 * mechanism, not a placeholder; visual face-based anchor picking is a
 * follow-on once mesh/part geometry is queryable post-commit (same
 * prerequisite noted in FORGE-277/279's own scope notes).
 */
export function AssemblyJointsEditor({ nodeId, parts, joints }: AssemblyJointsEditorProps) {
  const toast = useToast();
  const update = useUpdateAssemblyJoints();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState<JointType>('revolute');
  const [base, setBase] = useState('');
  const [follower, setFollower] = useState('');
  const [axisX, setAxisX] = useState('0');
  const [axisY, setAxisY] = useState('0');
  const [axisZ, setAxisZ] = useState('1');
  const [anchorX, setAnchorX] = useState('0');
  const [anchorY, setAnchorY] = useState('0');
  const [anchorZ, setAnchorZ] = useState('0');

  const partNames = parts.map((p) => p.link_name);

  const resetForm = () => {
    setName('');
    setType('revolute');
    setBase('');
    setFollower('');
    setAxisX('0');
    setAxisY('0');
    setAxisZ('1');
    setAnchorX('0');
    setAnchorY('0');
    setAnchorZ('0');
  };

  const save = (nextJoints: Joint[]) => {
    update.mutate(
      { nodeId, joints: nextJoints },
      {
        onError: () => toast.error('Could not save joints — check your gateway connection'),
      },
    );
  };

  const removeJoint = (jointName: string) => {
    save(joints.filter((j) => j.name !== jointName));
  };

  const submitNewJoint = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !base || !follower) return;
    const newJoint: Joint = {
      name: name.trim(),
      type,
      base,
      follower,
      axis: [Number(axisX) || 0, Number(axisY) || 0, Number(axisZ) || 0],
      anchor: [Number(anchorX) || 0, Number(anchorY) || 0, Number(anchorZ) || 0],
    };
    save([...joints, newJoint]);
    resetForm();
    setAdding(false);
  };

  return (
    <div className="tw-assembly-joints-editor" data-testid="assembly-joints-editor">
      {joints.length > 0 && (
        <ul className="tw-assembly-joints-list">
          {joints.map((joint) => (
            <li key={joint.name}>
              <span className="tw-assembly-joint-summary">
                <strong>{joint.name}</strong> · {joint.type} · {joint.base} → {joint.follower}
              </span>
              <button
                type="button"
                aria-label={`Delete joint ${joint.name}`}
                onClick={() => removeJoint(joint.name)}
                disabled={update.isPending}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {!adding ? (
        <button type="button" onClick={() => setAdding(true)} disabled={partNames.length < 2}>
          + Add mate/joint
        </button>
      ) : (
        <form onSubmit={submitNewJoint} className="tw-assembly-joint-form">
          <label htmlFor="joint-name">Name</label>
          <input
            id="joint-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. shoulder"
          />

          <label htmlFor="joint-type">Type</label>
          <select id="joint-type" value={type} onChange={(e) => setType(e.target.value as JointType)}>
            {JOINT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>

          <label htmlFor="joint-base">Base part</label>
          <select id="joint-base" required value={base} onChange={(e) => setBase(e.target.value)}>
            <option value="">Select…</option>
            {partNames.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          <label htmlFor="joint-follower">Follower part</label>
          <select
            id="joint-follower"
            required
            value={follower}
            onChange={(e) => setFollower(e.target.value)}
          >
            <option value="">Select…</option>
            {partNames.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          <label htmlFor="joint-axis-x">Axis</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input id="joint-axis-x" type="number" step="any" aria-label="Axis X" value={axisX} onChange={(e) => setAxisX(e.target.value)} />
            <input type="number" step="any" aria-label="Axis Y" value={axisY} onChange={(e) => setAxisY(e.target.value)} />
            <input type="number" step="any" aria-label="Axis Z" value={axisZ} onChange={(e) => setAxisZ(e.target.value)} />
          </div>

          <label htmlFor="joint-anchor-x">Anchor (mm)</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input id="joint-anchor-x" type="number" step="any" aria-label="Anchor X" value={anchorX} onChange={(e) => setAnchorX(e.target.value)} />
            <input type="number" step="any" aria-label="Anchor Y" value={anchorY} onChange={(e) => setAnchorY(e.target.value)} />
            <input type="number" step="any" aria-label="Anchor Z" value={anchorZ} onChange={(e) => setAnchorZ(e.target.value)} />
          </div>

          <div className="tw-assembly-joint-form-actions">
            <button type="button" onClick={() => { setAdding(false); resetForm(); }}>
              Cancel
            </button>
            <button type="submit" disabled={update.isPending}>
              {update.isPending ? 'Saving…' : 'Add joint'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
