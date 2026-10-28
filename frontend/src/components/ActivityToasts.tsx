import { useEffect, useRef, useState } from "react";
import type { ActivityToast } from "../activity";

export function ActivityToasts() {
  const [toasts, setToasts] = useState<ActivityToast[]>([]);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    function receive(event: Event) {
      const toast = (event as CustomEvent<ActivityToast>).detail;
      if (!toast?.message) return;
      setToasts((current) => [...current.slice(-2), toast]);
      const timer = setTimeout(() => {
        setToasts((current) => current.filter((item) => item.id !== toast.id));
        timers.current.delete(timer);
      }, 5000);
      timers.current.add(timer);
    }
    window.addEventListener("corral:activity", receive);
    return () => {
      window.removeEventListener("corral:activity", receive);
      timers.current.forEach(clearTimeout);
      timers.current.clear();
    };
  }, []);
  return <div className="activity-toasts" aria-live="polite" aria-relevant="additions">
    {toasts.map((toast) => <div className={`activity-toast ${toast.kind}`} role={toast.kind === "error" ? "alert" : "status"} key={toast.id}>
      <span className="activity-toast-mark" aria-hidden="true">{toast.kind === "success" ? "✓" : toast.kind === "error" ? "!" : "i"}</span>
      <p>{toast.message}</p>
      <button aria-label="Dismiss notification" onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}>×</button>
    </div>)}
  </div>;
}
