export type LeadIndicatorRow = {
  id: string;
  created_at?: string | null;
  received_at?: string | null;
};

export type NewLeadIndicatorSnapshot = {
  initialized: boolean;
  cursorTime: number | null;
  cursorIds: string[];
  unseenIds: string[];
  openedLeads: Array<{ id: string; timestamp: number }>;
};

export const emptyNewLeadIndicatorSnapshot: NewLeadIndicatorSnapshot = {
  initialized: false,
  cursorTime: null,
  cursorIds: [],
  unseenIds: [],
  openedLeads: [],
};

function timestampFor(lead: LeadIndicatorRow): number | null {
  const value = lead.created_at || lead.received_at;
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function sameValues(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export function newlyUnseenLeadIds(
  previous: NewLeadIndicatorSnapshot,
  next: NewLeadIndicatorSnapshot,
): string[] {
  const previouslyUnseen = new Set(previous.unseenIds);
  return next.unseenIds.filter((id) => !previouslyUnseen.has(id));
}

export function observeNewLeads(
  snapshot: NewLeadIndicatorSnapshot,
  leads: LeadIndicatorRow[],
): NewLeadIndicatorSnapshot {
  const openedRecords = snapshot.openedLeads ?? [];
  const observed = leads.flatMap((lead) => {
    const timestamp = timestampFor(lead);
    return timestamp === null ? [] : [{ id: lead.id, timestamp }];
  });

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
    };
  }

  let cursorTime = snapshot.cursorTime;
  const cursorIds = new Set(snapshot.cursorIds);
  const unseenIds = new Set(snapshot.unseenIds);
  const openedTimestamps = new Map(
    openedRecords.map(({ id, timestamp }) => [id, timestamp]),
  );

  for (const lead of observed) {
    const wouldBeNew =
      snapshot.cursorTime === null ||
      lead.timestamp > snapshot.cursorTime ||
      (lead.timestamp === snapshot.cursorTime &&
        !cursorIds.has(lead.id));
    if (wouldBeNew && !openedTimestamps.has(lead.id)) {
      unseenIds.add(lead.id);
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
  };
  if (
    snapshot.cursorTime === next.cursorTime &&
    sameValues(snapshot.cursorIds, next.cursorIds) &&
    sameValues(snapshot.unseenIds, next.unseenIds) &&
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
  if (
    unseenIds.length === snapshot.unseenIds.length &&
    openedLeads === openedRecords
  ) {
    return snapshot;
  }
  return {
    ...snapshot,
    unseenIds,
    openedLeads,
  };
}
