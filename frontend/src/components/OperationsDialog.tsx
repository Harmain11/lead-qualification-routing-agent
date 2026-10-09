import { useEffect, useRef, type ReactNode } from "react";
export function OperationsDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    element?.showModal();
    element
      ?.querySelector<HTMLElement>("[autofocus], input, textarea, select")
      ?.focus();
    return () => {
      element?.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="ops-dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="ops-dialog-head">
        <h2>{title}</h2>
        <button
          className="button-quiet"
          aria-label={"Close " + title}
          onClick={onClose}
        >
          Close ×
        </button>
      </div>
      {children}
    </dialog>
  );
}
