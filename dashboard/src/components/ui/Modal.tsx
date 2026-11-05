import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { clsx } from 'clsx';
import { Button } from './Button';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  /** The buttons, Cancel first and the main action last (see ModalActions). */
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  /** Wrap the body in a form, so Enter submits it. */
  onSubmit?: () => void;
}

/**
 * The Engineering view's modal: a native <dialog> opened with showModal(), as
 * CreateProjectDialog does. Escape and a click on the backdrop close it, focus
 * starts inside it and goes back to whatever opened it.
 */
export function Modal({ open, onClose, title, description, children, footer, size = 'md', onSubmit }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnTo.current = document.activeElement as HTMLElement | null;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    } else if (!open && dialog.open) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
      returnTo.current?.focus?.();
    }
  }, [open]);

  // Closing on unmount still hands focus back.
  useEffect(() => () => returnTo.current?.focus?.(), []);

  const body = (
    <>
      <div className="section-heading">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="icon-control" aria-label="Close" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      {description && <p className="mf-modal-description">{description}</p>}
      {children && <div className="mf-modal-body">{children}</div>}
      {footer && <div className="dialog-actions">{footer}</div>}
    </>
  );

  return (
    <dialog
      ref={ref}
      className={clsx('project-dialog mf-modal', size !== 'md' && `mf-modal-${size}`)}
      aria-labelledby={titleId}
      onCancel={(e) => {
        // Escape: the parent owns `open`, so it decides.
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {open &&
        (onSubmit ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              onSubmit();
            }}
          >
            {body}
          </form>
        ) : (
          body
        ))}
    </dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  /** `danger` for anything that deletes or cannot be undone. */
  tone?: 'primary' | 'danger';
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A yes-or-no question before an action that changes or removes something. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  tone = 'primary',
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="button" variant={tone} disabled={busy} onClick={onConfirm} autoFocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}
