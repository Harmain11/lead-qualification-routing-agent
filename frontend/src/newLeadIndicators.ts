export type LeadIndicatorRow = {
  id: string;
  created_at?: string | null;
  received_at?: string | null;
  email?: string | null;
};

type UnseenIdentityReference = {
  fingerprint: string;
  leadId: string;
};

export type NewLeadIndicatorSnapshot = {
  initialized: boolean;
  cursorTime: number | null;
  cursorIds: string[];
  unseenIds: string[];
  openedLeads: Array<{ id: string; timestamp: number }>;
  notifiedIdentityFingerprints: string[];
  unseenIdentityReferences: UnseenIdentityReference[];
};

export type ObserveNewLeadsOptions = {
  completeSnapshot?: boolean;
};

export const emptyNewLeadIndicatorSnapshot: NewLeadIndicatorSnapshot = {
  initialized: false,
  cursorTime: null,
  cursorIds: [],
  unseenIds: [],
  openedLeads: [],
  notifiedIdentityFingerprints: [],
  unseenIdentityReferences: [],
};

function timestampFor(lead: LeadIndicatorRow): number | null {
  const value = lead.created_at || lead.received_at;
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

async function emailFingerprint(email?: string | null): Promise<string | null> {
  const normalized = email?.trim().toLowerCase();
  if (!normalized) return null;
  if (!globalThis.crypto?.subtle) {
    throw new Error("Secure hashing is required for lead arrival deduplication.");
  }

  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(normalized),
  );
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return `email:${hex}`;
}

function sameValues(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function sameIdentityReferences(
  left: UnseenIdentityReference[],
  right: UnseenIdentityReference[],
): boolean {
  return (
    left.length === right.length &&
    left.every(
      (reference, index) =>
        reference.fingerprint === right[index]?.fingerprint &&
        reference.leadId === right[index]?.leadId,
    )
  );
}

export function newlyUnseenLeadIds(
  previous: NewLeadIndicatorSnapshot,
  next: NewLeadIndicatorSnapshot,
): string[] {
  const previouslyUnseen = new Set(previous.unseenIds);
  return next.unseenIds.filter((id) => !previouslyUnseen.has(id));
}

export async function observeNewLeads(
  snapshot: NewLeadIndicatorSnapshot,
  leads: LeadIndicatorRow[],
  { completeSnapshot = true }: ObserveNewLeadsOptions = {},
): Promise<NewLeadIndicatorSnapshot> {
  const openedRecords = snapshot.openedLeads ?? [];
  const currentLeadIds = new Set(leads.map((lead) => lead.id));
  const observedRows = await Promise.all(leads.map(async (lead) => {
    const timestamp = timestampFor(lead);
    return timestamp === null
      ? null
      : {
          id: lead.id,
          timestamp,
          fingerprint: await emailFingerprint(lead.email),
        };
  }));
  const observed = observedRows.filter(
    (lead): lead is NonNullable<typeof lead> => lead !== null,
  );

  if (!snapshot.initialized) {
    const cursorTime = observed.reduce<number | null>(
      (latest, lead) =>
        latest === null || lead.timestamp > latest ? lead.timestamp : latest,
      null,
    );
    const cursorIds = observed
      .filter((lead) => lead.timestamp === cursorTime)
      .map((lead) => lead.id);
    return {
      initialized: true,
      cursorTime,
      cursorIds,
      unseenIds: [],
      openedLeads: [],
      notifiedIdentityFingerprints: snapshot.notifiedIdentityFingerprints ?? [],
      unseenIdentityReferences: snapshot.unseenIdentityReferences ?? [],
    };
  }

  let cursorTime = snapshot.cursorTime;
  const cursorIds = new Set(snapshot.cursorIds);
  const unseenIds = new Set(snapshot.unseenIds);
  if (completeSnapshot) {
    for (const id of unseenIds) {
      if (!currentLeadIds.has(id)) unseenIds.delete(id);
    }
  }
  const notifiedIdentityFingerprints = new Set(
    snapshot.notifiedIdentityFingerprints ?? [],
  );
  const previouslyNotifiedFingerprints = new Set(notifiedIdentityFingerprints);
  const unseenIdentityReferences = new Map<string, UnseenIdentityReference>();
  for (const reference of snapshot.unseenIdentityReferences ?? []) {
    if (completeSnapshot && !currentLeadIds.has(reference.leadId)) continue;
    unseenIdentityReferences.set(
      `${reference.fingerprint}\u0000${reference.leadId}`,
      reference,
    );
  }

  // Backfill identity links for unread arrivals created before this field existed.
  for (const lead of observed) {
    if (lead.fingerprint && unseenIds.has(lead.id)) {
      notifiedIdentityFingerprints.add(lead.fingerprint);
      previouslyNotifiedFingerprints.add(lead.fingerprint);
      unseenIdentityReferences.set(
        `${lead.fingerprint}\u0000${lead.id}`,
        { fingerprint: lead.fingerprint, leadId: lead.id },
      );
    }
  }

  const fingerprintsAddedThisObservation = new Set<string>();
  const openedTimestamps = new Map(
    openedRecords.map(({ id, timestamp }) => [id, timestamp]),
  );

  for (const lead of observed) {
    const wouldBeNew =
      snapshot.cursorTime === null ||
      lead.timestamp > snapshot.cursorTime ||
      (lead.timestamp === snapshot.cursorTime &&
        !cursorIds.has(lead.id));

    const identityWasPreviouslyNotified =
      lead.fingerprint !== null &&
      previouslyNotifiedFingerprints.has(lead.fingerprint);
    if (identityWasPreviouslyNotified) {
      for (const [key, reference] of unseenIdentityReferences) {
        if (
          reference.fingerprint === lead.fingerprint &&
          reference.leadId !== lead.id
        ) {
          unseenIds.delete(reference.leadId);
          unseenIdentityReferences.delete(key);
        }
      }
      continue;
    }

    if (
      lead.fingerprint &&
      fingerprintsAddedThisObservation.has(lead.fingerprint)
    ) {
      continue;
    }

    if (wouldBeNew && openedTimestamps.has(lead.id) && lead.fingerprint) {
      notifiedIdentityFingerprints.add(lead.fingerprint);
      fingerprintsAddedThisObservation.add(lead.fingerprint);
      continue;
    }

    if (wouldBeNew && !openedTimestamps.has(lead.id)) {
      unseenIds.add(lead.id);
      if (lead.fingerprint) {
        notifiedIdentityFingerprints.add(lead.fingerprint);
        fingerprintsAddedThisObservation.add(lead.fingerprint);
        unseenIdentityReferences.set(
          `${lead.fingerprint}\u0000${lead.id}`,
          { fingerprint: lead.fingerprint, leadId: lead.id },
        );
      }
    }
  }

  const newestObservedTime = observed.reduce<number | null>(
    (latest, lead) =>
      latest === null || lead.timestamp > latest ? lead.timestamp : latest,
    null,
  );
  if (
    newestObservedTime !== null &&
    (cursorTime === null || newestObservedTime > cursorTime)
  ) {
    cursorTime = newestObservedTime;
    cursorIds.clear();
    for (const lead of observed) {
      if (lead.timestamp === cursorTime) cursorIds.add(lead.id);
    }
  } else if (newestObservedTime === cursorTime && cursorTime !== null) {
    for (const lead of observed) {
      if (lead.timestamp === cursorTime) cursorIds.add(lead.id);
    }
  }

  const openedLeads: NewLeadIndicatorSnapshot["openedLeads"] = [];
  for (const opened of openedRecords) {
    if (cursorTime !== null && opened.timestamp <= cursorTime) {
      if (opened.timestamp === cursorTime) cursorIds.add(opened.id);
    } else {
      openedLeads.push(opened);
    }
  }

  const next: NewLeadIndicatorSnapshot = {
    initialized: true,
    cursorTime,
    cursorIds: [...cursorIds],
    unseenIds: [...unseenIds],
    openedLeads,
    notifiedIdentityFingerprints: [...notifiedIdentityFingerprints],
    unseenIdentityReferences: [...unseenIdentityReferences.values()],
  };
  if (
    snapshot.cursorTime === next.cursorTime &&
    sameValues(snapshot.cursorIds, next.cursorIds) &&
    sameValues(snapshot.unseenIds, next.unseenIds) &&
    sameValues(
      snapshot.notifiedIdentityFingerprints ?? [],
      next.notifiedIdentityFingerprints,
    ) &&
    sameIdentityReferences(
      snapshot.unseenIdentityReferences ?? [],
      next.unseenIdentityReferences,
    ) &&
    openedRecords.length === next.openedLeads.length &&
    openedRecords.every(
      (opened, index) =>
        opened.id === next.openedLeads[index]?.id &&
        opened.timestamp === next.openedLeads[index]?.timestamp,
    )
  ) {
    return snapshot;
  }
  return next;
}

export function markLeadSeen(
  snapshot: NewLeadIndicatorSnapshot,
  leadId: string,
  createdAt?: string | null,
): NewLeadIndicatorSnapshot {
  const openedRecords = snapshot.openedLeads ?? [];
  const timestamp =
    createdAt && Number.isFinite(Date.parse(createdAt))
      ? Date.parse(createdAt)
      : null;
  const shouldRememberOpen =
    snapshot.initialized &&
    timestamp !== null &&
    (snapshot.cursorTime === null ||
      timestamp > snapshot.cursorTime ||
      (timestamp === snapshot.cursorTime &&
        !snapshot.cursorIds.includes(leadId))) &&
    !openedRecords.some(
      (opened) => opened.id === leadId && opened.timestamp === timestamp,
    );
  const openedLeads = shouldRememberOpen
    ? [
        ...openedRecords.filter((opened) => opened.id !== leadId),
        { id: leadId, timestamp },
      ]
    : openedRecords;
  const unseenIds = snapshot.unseenIds.filter((id) => id !== leadId);
  const unseenIdentityReferences = (
    snapshot.unseenIdentityReferences ?? []
  ).filter((reference) => reference.leadId !== leadId);
  if (
    unseenIds.length === snapshot.unseenIds.length &&
    openedLeads === openedRecords &&
    unseenIdentityReferences.length ===
      (snapshot.unseenIdentityReferences ?? []).length
  ) {
    return snapshot;
  }
  return {
    ...snapshot,
    unseenIds,
    openedLeads,
    unseenIdentityReferences,
  };
}
