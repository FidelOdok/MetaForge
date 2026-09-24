import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { ArrowRight, X } from 'lucide-react';
import { useCreateProject } from '../../hooks/use-projects';

export interface CreateProjectDialogHandle {
  open: () => void;
  close: () => void;
}

interface CreateProjectDialogProps {
  /** Element that receives focus again once the dialog closes. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}

/**
 * Native `<dialog>` for creating a project. Falls back to the `open`
 * attribute where `showModal` is unavailable (jsdom, very old browsers).
 */
export const CreateProjectDialog = forwardRef<CreateProjectDialogHandle, CreateProjectDialogProps>(
  function CreateProjectDialog({ returnFocusRef }, ref) {
    const dialogRef = useRef<HTMLDialogElement>(null);
    const createProject = useCreateProject();
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');

    const open = () => {
      createProject.reset?.();
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

    const handleSubmit = (e: React.FormEvent) => {
      e.preventDefault();
      if (!name.trim() || createProject.isPending) return;
      createProject.mutate(
        { name: name.trim(), description: description.trim() },
        {
          onSuccess: () => {
            setName('');
            setDescription('');
            close();
          },
        },
      );
    };

    return (
      <dialog
        ref={dialogRef}
        className="project-dialog"
        aria-labelledby="create-title"
        onClose={() => returnFocusRef?.current?.focus()}
      >
        <form onSubmit={handleSubmit}>
          <div className="section-heading">
            <h2 id="create-title">Create a project</h2>
            <button type="button" className="icon-control" aria-label="Close create project" onClick={close}>
              <X size={20} />
            </button>
          </div>
          <p>Give your next engineering effort a home.</p>
          <label htmlFor="project-name">Project name</label>
          <input
            autoFocus
            required
            maxLength={200}
            id="project-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Autonomous inspection rover"
          />
          <label htmlFor="project-description">
            Description <span>(optional)</span>
          </label>
          <textarea
            id="project-description"
            maxLength={2000}
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What are you building, and what should it achieve?"
          />
          {createProject.isError && (
            <p role="alert" className="form-error">
              Project could not be created. Check your gateway connection and try again.
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="action-secondary" onClick={close}>
              Cancel
            </button>
            <button className="action-primary" type="submit" disabled={!name.trim() || createProject.isPending}>
              {createProject.isPending ? 'Creating…' : 'Create project'}
              <ArrowRight size={16} />
            </button>
          </div>
        </form>
      </dialog>
    );
  },
);
