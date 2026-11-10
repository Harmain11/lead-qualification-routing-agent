import { useEffect, useState } from "react";
import { Button, Sheet } from "./primitives";

export type ConfirmOptions = {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};

type Pending = ConfirmOptions & { resolve: (confirmed: boolean) => void };

const EVENT = "corral:confirm";

/** Ask the person to confirm a consequential action. Resolves false when dismissed. */
export function confirmAction(options: ConfirmOptions | string): Promise<boolean> {
  const config = typeof options === "string" ? { title: options } : options;
  return new Promise((resolve) => {
    const unhandled = window.dispatchEvent(new CustomEvent<Pending>(EVENT, { detail: { ...config, resolve }, cancelable: true }));
    // No host mounted (for example in tests): fail safe by not performing the action.
    if (unhandled) window.setTimeout(() => resolve(false), 0);
  });
}

/** Mount once near the app root. Renders one confirmation at a time. */
export function ConfirmHost() {
  const [pending, setPending] = useState<Pending | null>(null);
  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<Pending>).detail;
      event.preventDefault();
      setPending((current) => {
        current?.resolve(false);
        return detail;
      });
    };
    window.addEventListener(EVENT, receive);
    return () => window.removeEventListener(EVENT, receive);
  }, []);
  if (!pending) return null;
  const finish = (confirmed: boolean) => {
    pending.resolve(confirmed);
    setPending(null);
  };
  return (
    <Sheet
      title={pending.title}
      onClose={() => finish(false)}
      width={440}
      footer={
        <>
          <Button kind="gray" onClick={() => finish(false)} data-autofocus={pending.tone === "danger" ? "" : undefined}>{pending.cancelLabel ?? "Cancel"}</Button>
          <Button kind={pending.tone === "danger" ? "destructive" : "filled"} onClick={() => finish(true)} data-autofocus={pending.tone !== "danger" ? "" : undefined}>
            {pending.confirmLabel ?? "Confirm"}
          </Button>
        </>
      }
    >
      {pending.message && <p className="v-confirm-message">{pending.message}</p>}
    </Sheet>
  );
}
