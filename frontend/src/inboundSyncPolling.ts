import type { CrmInboundSyncRun, CrmInboundSyncState } from "./types";

type PollOptions = {
  queuedAt: string;
  getRuns: () => Promise<CrmInboundSyncRun[]>;
  getState: () => Promise<CrmInboundSyncState>;
  onProgress: (message: string) => void;
  wait?: () => Promise<void>;
};

export async function pollInboundSync({
  queuedAt,
  getRuns,
  getState,
  onProgress,
  wait = () => new Promise((resolve) => window.setTimeout(resolve, 1800)),
}: PollOptions): Promise<CrmInboundSyncRun> {
  const acceptedAt = Date.parse(queuedAt);
  while (true) {
    await wait();
    const [runs, state] = await Promise.all([getRuns(), getState()]);
    const run = runs.find((candidate) => Date.parse(candidate.created_at) >= acceptedAt);
    if (run) {
      const saved = run.records_created + run.records_updated;
      onProgress(
        `Gmail sync ${run.status}: ${run.records_processed} processed, ${saved} leads saved, ${run.records_skipped} skipped, ${run.records_failed} failed.`,
      );
      if (!["queued", "running"].includes(run.status)) return run;
    } else if (state.status === "failed") {
      throw new Error(state.last_error || "Gmail sync worker failed before processing began.");
    } else {
      onProgress("Gmail sync is queued; waiting for a worker to start.");
    }
  }
}
