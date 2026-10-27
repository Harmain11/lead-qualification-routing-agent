import { useCallback, useEffect, useRef, useState } from "react";
import {
  emptyNewLeadIndicatorSnapshot,
  markLeadSeen as removeSeenLead,
  newlyUnseenLeadIds,
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
      !parsed ||
      typeof parsed !== "object" ||
      typeof parsed.initialized !== "boolean" ||
      !Array.isArray(parsed.cursorIds) ||
      !Array.isArray(parsed.unseenIds)
    ) {
      return emptyNewLeadIndicatorSnapshot;
    }
    return {
      ...parsed,
      openedLeads: Array.isArray(parsed.openedLeads) ? parsed.openedLeads : [],
      notifiedIdentityFingerprints: Array.isArray(
        parsed.notifiedIdentityFingerprints,
      )
        ? parsed.notifiedIdentityFingerprints.filter(
            (value: unknown): value is string => typeof value === "string",
          )
        : [],
      unseenIdentityReferences: Array.isArray(parsed.unseenIdentityReferences)
        ? parsed.unseenIdentityReferences.filter(
            (
              value: unknown,
            ): value is NewLeadIndicatorSnapshot["unseenIdentityReferences"][number] =>
              value !== null &&
              typeof value === "object" &&
              "fingerprint" in value &&
              "leadId" in value &&
              typeof value.fingerprint === "string" &&
              typeof value.leadId === "string",
          )
        : [],
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
  const storedRef = useRef(stored);
  const observationVersion = useRef(0);

  useEffect(() => {
    observationVersion.current += 1;
    const next = { key, value: readSnapshot(key) };
    storedRef.current = next;
    setStored(next);
  }, [key]);

  useEffect(() => {
    if (stored.key === key) writeSnapshot(key, stored.value);
  }, [key, stored]);

  const snapshot =
    stored.key === key ? stored.value : emptyNewLeadIndicatorSnapshot;

  const observeLeadSnapshot = useCallback(
    async (leads: LeadIndicatorRow[]) => {
      if (!key) return [];
      const version = ++observationVersion.current;
      const previous = storedRef.current.key === key
        ? storedRef.current.value
        : readSnapshot(key);
      const next = await observeNewLeads(previous, leads);
      if (
        version !== observationVersion.current ||
        storedRef.current.key !== key
      ) {
        return [];
      }
      const arrivals = newlyUnseenLeadIds(previous, next);
      if (next !== previous) {
        const updated = { key, value: next };
        storedRef.current = updated;
        setStored(updated);
      }
      return arrivals;
    },
    [key],
  );

  const markSeen = useCallback(
    (leadId: string, createdAt?: string | null) => {
      if (!key) return;
      const previous = storedRef.current.key === key
        ? storedRef.current.value
        : readSnapshot(key);
      const next = removeSeenLead(previous, leadId, createdAt);
      if (next === previous) return;
      const updated = { key, value: next };
      storedRef.current = updated;
      setStored(updated);
    },
    [key],
  );

  return {
    unseenLeadIds: snapshot.unseenIds,
    observeLeadSnapshot,
    markSeen,
  };
}
