import { useCallback, useEffect, useRef, useState } from "react";
import type { ActivityToast } from "../activity";
import { Icon, type IconName } from "../v3/ui/Icon";

const LIFETIME_MS = { success: 4000, info: 6000 } as const;
const ICONS: Record<ActivityToast["kind"], IconName> = { success: "check", error: "alert", info: "info" };

type Timer = { handle: ReturnType<typeof setTimeout>; due: number; remaining: number };

export function ActivityToasts() {
  const [toasts, setToasts] = useState<ActivityToast[]>([]);
  const timers = useRef(new Map<string, Timer>());
  const paused = useRef(false);

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer.handle);
    timers.current.delete(id);
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const schedule = useCallback((id: string, delay: number) => {
    const handle = setTimeout(() => dismiss(id), delay);
    timers.current.set(id, { handle, due: Date.now() + delay, remaining: delay });
  }, [dismiss]);

  useEffect(() => {
    function receive(event: Event) {
      const toast = (event as CustomEvent<ActivityToast>).detail;
      if (!toast?.message) return;
      setToasts((current) => {
        const kept = current.filter((item) => item.message !== toast.message || item.kind !== toast.kind);
        const overflow = kept.slice(0, Math.max(0, kept.length - 2));
        overflow.forEach((item) => {
          const timer = timers.current.get(item.id);
          if (timer) clearTimeout(timer.handle);
          timers.current.delete(item.id);
        });
        return [...kept.slice(-2), toast];
      });
      // Errors stay until dismissed so nobody misses a failure.
      if (toast.kind !== "error" && !paused.current) schedule(toast.id, LIFETIME_MS[toast.kind]);
      else if (toast.kind !== "error") timers.current.set(toast.id, { handle: setTimeout(() => {}, 0), due: 0, remaining: LIFETIME_MS[toast.kind] });
    }
    const active = timers.current;
    window.addEventListener("corral:activity", receive);
    return () => {
      window.removeEventListener("corral:activity", receive);
      active.forEach((timer) => clearTimeout(timer.handle));
      active.clear();
    };
  }, [schedule]);

  const pause = () => {
    if (paused.current) return;
    paused.current = true;
    timers.current.forEach((timer, id) => {
      clearTimeout(timer.handle);
      timers.current.set(id, { ...timer, remaining: Math.max(800, timer.due - Date.now()) });
    });
  };
  const resume = () => {
    if (!paused.current) return;
    paused.current = false;
    [...timers.current.entries()].forEach(([id, timer]) => schedule(id, timer.remaining));
  };

  if (!toasts.length) return null;
  return (
    <section
      className="activity-toasts"
      aria-label="Notifications"
      onMouseEnter={pause}
      onMouseLeave={resume}
      onFocus={pause}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) resume(); }}
    >
      {toasts.map((toast) => (
        <div className="activity-toast" data-kind={toast.kind} role={toast.kind === "error" ? "alert" : "status"} key={toast.id}>
          <span className="activity-toast-mark" aria-hidden="true"><Icon name={ICONS[toast.kind]} size={16} /></span>
          <p>{toast.message}</p>
          {toast.action && (
            <button type="button" className="activity-toast-action" onClick={() => { toast.action?.run(); dismiss(toast.id); }}>
              {toast.action.label}
            </button>
          )}
          <button type="button" className="activity-toast-close" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}>
            <Icon name="close" size={14} />
          </button>
        </div>
      ))}
    </section>
  );
}
