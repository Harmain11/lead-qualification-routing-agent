import { useCallback, useEffect, useState } from "react";
import {
  listAssignmentNotifications,
  markAssignmentNotificationRead,
  type AssignmentNotification,
} from "./api";

type Snapshot = {
  key: string | null;
  items: AssignmentNotification[];
};

export function useAssignmentNotifications(
  userId: string | undefined,
  workspaceId: string | undefined,
  enabled: boolean,
) {
  const key = userId && workspaceId ? `${userId}:${workspaceId}` : null;
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: null, items: [] });
  const active = Boolean(key && enabled);

  const refresh = useCallback(async () => {
    if (!key || !active) return;
    try {
      const items = await listAssignmentNotifications();
      setSnapshot({ key, items });
    } catch {
      // Keep the last successful unread list visible during a temporary network error.
    }
  }, [active, key]);

  useEffect(() => {
    if (!active || !key) {
      setSnapshot({ key, items: [] });
      return;
    }
    setSnapshot((current) =>
      current.key === key ? current : { key, items: [] },
    );
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [active, key, refresh]);

  const markRead = useCallback(
    async (id: string) => {
      if (!key) return;
      setSnapshot((current) =>
        current.key === key
          ? { ...current, items: current.items.filter((item) => item.id !== id) }
          : current,
      );
      try {
        await markAssignmentNotificationRead(id);
      } catch {
        await refresh();
      }
    },
    [key, refresh],
  );

  return {
    notifications: snapshot.key === key ? snapshot.items : [],
    markRead,
  };
}
