import { useCallback, useEffect, useState } from "react";
import {
  emptyNewLeadIndicatorSnapshot,
  markLeadSeen as removeSeenLead,
  observeNewLeads,
  type LeadIndicatorRow,
  type NewLeadIndicatorSnapshot,
} from "./newLeadIndicators";

type StoredSnapshot = {
  key: string | null;
  value: NewLeadIndicatorSnapshot;
};

function readSnapshot(key: string | null): NewLeadIndicatorSnapshot {
  if (!key || typeof window === "undefined") return emptyNewLeadIndicatorSnapshot;
  try {
    const value = window.localStorage.getItem(key);
    if (!value) return emptyNewLeadIndicatorSnapshot;
    const parsed = JSON.parse(value) as NewLeadIndicatorSnapshot;
    if (
      typeof parsed.initialized !== "boolean" ||
      !Array.isArray(parsed.cursorIds) ||
      !Array.isArray(parsed.unseenIds)
    ) {
      return emptyNewLeadIndicatorSnapshot;
    }
    return {
      ...parsed,
      openedLeads: Array.isArray(parsed.openedLeads) ? parsed.openedLeads : [],
    };
  } catch {
    return emptyNewLeadIndicatorSnapshot;
  }
}

function writeSnapshot(
  key: string | null,
  snapshot: NewLeadIndicatorSnapshot,
): void {
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(snapshot));
  } catch {
    // Keep the indicator functional for this session if storage is unavailable.
  }
}

export function useNewLeadIndicators(
  userId: string | undefined,
  workspaceId: string | undefined,
) {
  const key =
    userId && workspaceId
      ? `corral-new-lead-indicators:v1:${userId}:${workspaceId}`
      : null;
  const [stored, setStored] = useState<StoredSnapshot>(() => ({
    key,
    value: readSnapshot(key),
  }));

  useEffect(() => {
    setStored({ key, value: readSnapshot(key) });
  }, [key]);

  useEffect(() => {
    if (stored.key === key) writeSnapshot(key, stored.value);
  }, [key, stored]);

  const snapshot =
    stored.key === key ? stored.value : emptyNewLeadIndicatorSnapshot;

  const observeLeadSnapshot = useCallback(
    (leads: LeadIndicatorRow[]) => {
      if (!key) return;
      setStored((current) => {
        const previous =
          current.key === key ? current.value : readSnapshot(key);
        const next = observeNewLeads(previous, leads);
        if (next === previous) return current;
        return { key, value: next };
      });
    },
    [key],
  );

  const markSeen = useCallback(
    (leadId: string, createdAt?: string | null) => {
      if (!key) return;
      setStored((current) => {
        const previous =
          current.key === key ? current.value : readSnapshot(key);
        const next = removeSeenLead(previous, leadId, createdAt);
        if (next === previous) return current;
        return { key, value: next };
      });
    },
    [key],
  );

  return {
    unseenLeadIds: snapshot.unseenIds,
    observeLeadSnapshot,
    markSeen,
  };
}
