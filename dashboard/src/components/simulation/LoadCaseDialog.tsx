import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { ArrowRight, X } from 'lucide-react';
import { useCreateLoadCase, useNamedFaces } from '../../hooks/use-load-cases';
import { FacePickerViewer } from './FacePickerViewer';

export interface LoadCaseDialogHandle {
  open: () => void;
  close: () => void;
}

interface LoadCaseDialogProps {
  projectId: string;
  /** Element that receives focus again once the dialog closes. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}

/**
 * Native `<dialog>` for authoring a load case (FORGE-278) — mirrors
 * ``CreateProjectDialog``'s structure/CSS classes exactly (``.project-dialog``
 * is generic modal chrome despite its name; nothing here is project-specific).
 */
export const LoadCaseDialog = forwardRef<LoadCaseDialogHandle, LoadCaseDialogProps>(
  function LoadCaseDialog({ projectId, returnFocusRef }, ref) {
    const dialogRef = useRef<HTMLDialogElement>(null);
    const createLoadCase = useCreateLoadCase(projectId);
    const [name, setName] = useState('');
    const [materialName, setMaterialName] = useState('steel');
    const [fixedNodeSet, setFixedNodeSet] = useState('');
    const [loadNodeSet, setLoadNodeSet] = useState('');
    const [forceX, setForceX] = useState('0');
    const [forceY, setForceY] = useState('0');
    const [forceZ, setForceZ] = useState('0');
    const [sourceOfLoads, setSourceOfLoads] = useState('');
    // FORGE-277: geometric face selection — a mesh file the user already has
    // in hand from an earlier freecad.generate_mesh turn unlocks a 3D face
    // picker instead of typing an opaque gmsh group name below.
    const [meshFile, setMeshFile] = useState('');
    const [pickMode, setPickMode] = useState<'fixed' | 'load'>('fixed');
    const { data: namedFaces, isLoading: facesLoading } = useNamedFaces(
      meshFile.trim() || undefined,
    );

    const open = () => {
      createLoadCase.reset?.();
      const dialog = dialogRef.current;
      if (!dialog) return;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    };

    const close = () => {
      const dialog = dialogRef.current;
      if (dialog) {
        if (typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
      }
      returnFocusRef?.current?.focus();
    };

    useImperativeHandle(ref, () => ({ open, close }));

    const isValid = name.trim() && fixedNodeSet.trim() && loadNodeSet.trim();

    const handleSubmit = (e: React.FormEvent) => {
      e.preventDefault();
      if (!isValid || createLoadCase.isPending) return;
      createLoadCase.mutate(
        {
          name: name.trim(),
          projectId,
          material: { name: materialName.trim() || 'steel' },
          fixedNodeSet: fixedNodeSet.trim(),
          loadNodeSet: loadNodeSet.trim(),
          loadForceN: [Number(forceX) || 0, Number(forceY) || 0, Number(forceZ) || 0],
          ...(sourceOfLoads.trim() ? { sourceOfLoads: sourceOfLoads.trim() } : {}),
        },
        {
          onSuccess: () => {
            setName('');
            setFixedNodeSet('');
            setLoadNodeSet('');
            setForceX('0');
            setForceY('0');
            setForceZ('0');
            setSourceOfLoads('');
            setMeshFile('');
            setPickMode('fixed');
            close();
          },
        },
      );
    };

    return (
      <dialog
        ref={dialogRef}
        className="project-dialog"
        aria-labelledby="load-case-title"
        onClose={() => returnFocusRef?.current?.focus()}
      >
        <form onSubmit={handleSubmit}>
          <div className="section-heading">
            <h2 id="load-case-title">New load case</h2>
            <button type="button" className="icon-control" aria-label="Close new load case" onClick={close}>
              <X size={20} />
            </button>
          </div>
          <p>Define a boundary condition once, reuse it across design versions.</p>

          <label htmlFor="load-case-name">Name</label>
          <input
            autoFocus
            required
            maxLength={200}
            id="load-case-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Cantilever static load"
          />

          <label htmlFor="load-case-material">Material</label>
          <input
            id="load-case-material"
            value={materialName}
            onChange={(e) => setMaterialName(e.target.value)}
            placeholder="e.g. steel, aluminum_6061"
          />

          <label htmlFor="load-case-mesh-file">
            Mesh file <span>(optional — pick faces visually instead of typing names)</span>
          </label>
          <input
            id="load-case-mesh-file"
            value={meshFile}
            onChange={(e) => setMeshFile(e.target.value)}
            placeholder="e.g. /workspace/bracket.inp — from a generate_mesh call"
          />

          {meshFile.trim() && facesLoading && (
            <p className="font-mono text-xs text-on-surface-variant">Loading named faces…</p>
          )}

          {namedFaces && namedFaces.faces.length > 0 && (
            <>
              <div
                role="group"
                aria-label="Face pick mode"
                style={{ display: 'flex', gap: '4px', marginBottom: '8px' }}
              >
                <button
                  type="button"
                  aria-pressed={pickMode === 'fixed'}
                  onClick={() => setPickMode('fixed')}
                  className={pickMode === 'fixed' ? 'action-primary' : 'action-secondary'}
                >
                  Pick fixed face
                </button>
                <button
                  type="button"
                  aria-pressed={pickMode === 'load'}
                  onClick={() => setPickMode('load')}
                  className={pickMode === 'load' ? 'action-primary' : 'action-secondary'}
                >
                  Pick load face
                </button>
              </div>
              <FacePickerViewer
                faces={namedFaces.faces}
                fixedFace={fixedNodeSet}
                loadFace={loadNodeSet}
                pickMode={pickMode}
                onPickFixed={setFixedNodeSet}
                onPickLoad={setLoadNodeSet}
              />
            </>
          )}

          <label htmlFor="load-case-fixed">
            Fixed node set <span>(from freecad.generate_mesh's own 'faces' table)</span>
          </label>
          <input
            required
            id="load-case-fixed"
            value={fixedNodeSet}
            onChange={(e) => setFixedNodeSet(e.target.value)}
            placeholder="e.g. Surface1"
          />

          <label htmlFor="load-case-load-set">Load node set</label>
          <input
            required
            id="load-case-load-set"
            value={loadNodeSet}
            onChange={(e) => setLoadNodeSet(e.target.value)}
            placeholder="e.g. Surface2"
          />

          <label htmlFor="load-case-force-x">Load force (N)</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              id="load-case-force-x"
              type="number"
              aria-label="Force X (N)"
              value={forceX}
              onChange={(e) => setForceX(e.target.value)}
              placeholder="X"
            />
            <input
              type="number"
              aria-label="Force Y (N)"
              value={forceY}
              onChange={(e) => setForceY(e.target.value)}
              placeholder="Y"
            />
            <input
              type="number"
              aria-label="Force Z (N)"
              value={forceZ}
              onChange={(e) => setForceZ(e.target.value)}
              placeholder="Z"
            />
          </div>

          <label htmlFor="load-case-source">
            Source of loads <span>(optional)</span>
          </label>
          <input
            id="load-case-source"
            value={sourceOfLoads}
            onChange={(e) => setSourceOfLoads(e.target.value)}
            placeholder="e.g. Requirement REQ-12, hand calc"
          />

          {createLoadCase.isError && (
            <p role="alert" className="form-error">
              Load case could not be created. Check your gateway connection and try again.
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="action-secondary" onClick={close}>
              Cancel
            </button>
            <button className="action-primary" type="submit" disabled={!isValid || createLoadCase.isPending}>
              {createLoadCase.isPending ? 'Creating…' : 'Create load case'}
              <ArrowRight size={16} />
            </button>
          </div>
        </form>
      </dialog>
    );
  },
);
