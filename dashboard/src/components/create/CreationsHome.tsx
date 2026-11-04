import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Archive, ArchiveRestore, ArrowUp, Copy, ImagePlus, Pencil, Trash2 } from 'lucide-react';
import { useCreateProject, useDeleteProject, useProjects, useUpdateProject } from '../../hooks/use-projects';
import { deleteCreation, duplicateCreation, getCreation, putBrief, refusalOf } from '../../api/endpoints/create';
import { useCreateStore } from '../../store/create-store';
import type { Project } from '../../types/project';
import { AutoGrowTextarea } from './AutoGrowTextarea';

const EXAMPLES = [
  'A hook that clamps under my desk and holds headphones',
  'A wall bracket for a 30 cm shelf, matte white',
  'An enclosure for a Raspberry Pi 5 with a fan',
  'A replacement knob for my oven, 6 mm D-shaft',
];

/** A name for a new creation, from the first words of what the person wrote. */
export function nameFromPrompt(prompt: string): string {
  const words = prompt.replace(/\s+/g, ' ').trim().split(' ').slice(0, 6).join(' ');
  const clean = words.replace(/[.,;:!?]+$/, '');
  return (clean.charAt(0).toUpperCase() + clean.slice(1)).slice(0, 60) || 'New creation';
}

/** Start something new, or pick up a creation where it was left. */
export function CreationsHome() {
  const navigate = useNavigate();
  const projects = useProjects();
  const createProject = useCreateProject();
  const [prompt, setPrompt] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const starting = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const start = async () => {
    const text = prompt.trim();
    if (!text || starting.current) return;
    starting.current = true;
    setError('');
    try {
      const base = nameFromPrompt(text);
      const taken = new Set((projects.data ?? []).map((p) => p.name.toLowerCase()));
      let name = base;
      for (let i = 2; taken.has(name.toLowerCase()); i += 1) name = `${base} ${i}`;
      const project = await createProject.mutateAsync({ name, description: text.slice(0, 2000) });
      // The request becomes the first line of the brief, in the person's own words.
      await putBrief(
        project.id,
        { items: { intended_use: { value: text, status: 'confirmed', source: 'user' } }, measurements: [], assumptions: [], references: [] },
        { expected_version: 0, summary: 'Brief started from the first request' },
      );
      const store = useCreateStore.getState();
      if (files.length) store.attach(project.id, files);
      store.setIntent(project.id, 'ask');
      store.setDraft(
        project.id,
        `${text}\n\nHelp me shape this into a product: say what you understood, list the assumptions you are making, and ask me at most three short questions that matter most right now.`,
      );
      navigate(`/create/${project.id}?start=1`);
    } catch (e) {
      setError(refusalOf(e));
      starting.current = false;
    }
  };

  const list = (projects.data ?? [])
    .filter((p) => (showArchived ? p.status === 'archived' : p.status !== 'archived'))
    .sort((a, b) => (b.lastUpdated ?? '').localeCompare(a.lastUpdated ?? ''));

  return (
    <div className="cv-home">
      <section className="cv-hero">
        <p className="cv-eyebrow">Create</p>
        <h1>What do you want to make?</h1>
        <p className="cv-muted">
          Describe it in your own words. MetaForge shapes it with you, designs it, helps you make it at home, in a workshop
          or with a manufacturer, and checks what comes out.
        </p>
        <form
          className="cv-composer cv-hero-composer"
          onSubmit={(e) => {
            e.preventDefault();
            void start();
          }}
        >
          {files.length > 0 && (
            <ul className="cv-attachments">
              {files.map((f) => (
                <li key={f.name} className="cv-attachment">
                  <span className="cv-attachment-name">{f.name}</span>
                  <span className="cv-attachment-state">Uploads when you start</span>
                </li>
              ))}
            </ul>
          )}
          <div className="cv-composer-row">
            <button
              type="button"
              className="cv-icon-btn cv-attach"
              aria-label="Start from a photo, sketch or design file"
              title="Start from a photo, sketch or design file"
              onClick={() => fileInput.current?.click()}
            >
              <ImagePlus size={18} />
            </button>
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              accept="image/*,.pdf,.step,.stp,.stl,.3mf,.obj,.glb,.dxf,.svg"
              onChange={(e) => {
                setFiles((prev) => [...prev, ...Array.from(e.target.files ?? [])]);
                e.target.value = '';
              }}
            />
            <AutoGrowTextarea
              className="cv-textarea"
              aria-label="Describe what you want to make"
              placeholder="A bracket that holds my bike against the wall…"
              value={prompt}
              autoFocus
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void start();
                }
              }}
            />
            <button type="submit" className="cv-send" disabled={!prompt.trim() || createProject.isPending} aria-label="Start creating">
              <ArrowUp size={18} />
            </button>
          </div>
        </form>
        {error && <p className="cv-error">{error}</p>}
        <div className="cv-examples" aria-label="Examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="cv-chip-btn" onClick={() => setPrompt(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </section>

      <section className="cv-creations" aria-labelledby="cv-creations-title">
        <div className="cv-creations-head">
          <h2 id="cv-creations-title">{showArchived ? 'Archived creations' : 'Your creations'}</h2>
          <button type="button" className="cv-link" onClick={() => setShowArchived((v) => !v)}>
            {showArchived ? 'Show current' : 'Show archived'}
          </button>
        </div>
        {projects.isLoading && <p className="cv-muted">Loading…</p>}
        {projects.isError && <p className="cv-error">Creations could not be loaded: {refusalOf(projects.error)}</p>}
        {!projects.isLoading && list.length === 0 && (
          <p className="cv-muted">{showArchived ? 'Nothing archived.' : 'Nothing yet. Start above, or open an existing project.'}</p>
        )}
        <ul className="cv-creation-list">
          {list.map((p) => (
            <CreationRow key={p.id} project={p} onOpen={() => navigate(`/create/${p.id}`)} />
          ))}
        </ul>
      </section>
    </div>
  );
}

function CreationRow({ project, onOpen }: { project: Project; onOpen: () => void }) {
  const navigate = useNavigate();
  const update = useUpdateProject();
  const remove = useDeleteProject();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(project.name);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const duplicate = async () => {
    setBusy(true);
    setError('');
    try {
      const copy = await duplicateCreation(project.id, `${project.name} (copy)`);
      navigate(`/create/${copy.project_id}`);
    } catch (e) {
      setError(refusalOf(e));
    } finally {
      setBusy(false);
    }
  };

  const destroy = async () => {
    setBusy(true);
    setError('');
    try {
      // The Create records go first: that refuses while a build is released or being made.
      await deleteCreation(project.id);
      await remove.mutateAsync(project.id);
    } catch (e) {
      setError(refusalOf(e));
      setBusy(false);
    }
  };

  return (
    <li className="cv-creation">
      {renaming ? (
        <form
          className="cv-rename"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            update.mutate(
              { id: project.id, payload: { name: name.trim() } },
              { onSuccess: () => setRenaming(false), onError: (er) => setError(refusalOf(er)) },
            );
          }}
        >
          <input className="cv-input" aria-label="Creation name" value={name} autoFocus onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="cv-btn cv-btn-primary">
            Save
          </button>
          <button type="button" className="cv-btn" onClick={() => setRenaming(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <button type="button" className="cv-creation-open" onClick={onOpen}>
          <strong>{project.name}</strong>
          <span className="cv-muted">
            {project.work_products.length} item{project.work_products.length === 1 ? '' : 's'}
            {project.lastUpdated ? ` · updated ${new Date(project.lastUpdated).toLocaleDateString()}` : ''}
          </span>
        </button>
      )}
      <div className="cv-creation-actions">
        <button type="button" className="cv-icon-btn" aria-label={`Rename ${project.name}`} title="Rename" onClick={() => setRenaming(true)}>
          <Pencil size={14} />
        </button>
        <button type="button" className="cv-icon-btn" aria-label={`Duplicate ${project.name}`} title="Duplicate" disabled={busy} onClick={() => void duplicate()}>
          <Copy size={14} />
        </button>
        <button
          type="button"
          className="cv-icon-btn"
          aria-label={project.status === 'archived' ? `Restore ${project.name}` : `Archive ${project.name}`}
          title={project.status === 'archived' ? 'Restore' : 'Archive'}
          onClick={() =>
            update.mutate({ id: project.id, payload: { status: project.status === 'archived' ? 'active' : 'archived' } })
          }
        >
          {project.status === 'archived' ? <ArchiveRestore size={14} /> : <Archive size={14} />}
        </button>
        <button type="button" className="cv-icon-btn" aria-label={`Delete ${project.name}`} title="Delete" onClick={() => setConfirming(true)}>
          <Trash2 size={14} />
        </button>
      </div>
      {confirming && <DeleteConfirm project={project} busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void destroy()} />}
      {error && <p className="cv-error">{error}</p>}
    </li>
  );
}

function DeleteConfirm({
  project,
  busy,
  onCancel,
  onConfirm,
}: {
  project: Project;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [active, setActive] = useState<number | null>(null);
  // Say up front when a build is still released or being made.
  useEffect(() => {
    let live = true;
    getCreation(project.id)
      .then((v) => live && setActive(v.creation.builds.filter((b) => b.status === 'released' || b.status === 'making').length))
      .catch(() => live && setActive(0));
    return () => {
      live = false;
    };
  }, [project.id]);
  return (
    <div className="cv-banner is-warning" role="alertdialog" aria-label={`Delete ${project.name}`}>
      <p>
        Deleting <strong>{project.name}</strong> removes the creation, its brief, its builds and inspection records, and
        its links to {project.work_products.length} file{project.work_products.length === 1 ? '' : 's'}. The files stay in
        the digital twin. Orders already placed with a manufacturer are not cancelled.
      </p>
      {active ? (
        <p>
          {active} build{active === 1 ? ' is' : 's are'} still released or being made. Finish or cancel {active === 1 ? 'it' : 'them'} first;
          deleting never stops a machine.
        </p>
      ) : null}
      <div className="cv-actions">
        <button type="button" className="cv-btn cv-btn-danger" disabled={busy || !!active} onClick={onConfirm}>
          Delete
        </button>
        <button type="button" className="cv-btn" onClick={onCancel}>
          Keep it
        </button>
      </div>
    </div>
  );
}
