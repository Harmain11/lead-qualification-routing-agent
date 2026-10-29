import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listLeads } from "../../api";
import type { Lead, PipelineAnalytics } from "../../types";
import { useDeals, viewDeal, type DealView } from "./dealStore";

const PAGE = 100;
const MAX_LEADS = 600;

/**
 * Every lead in the workspace (up to 600), refreshed on demand and every 20s
 * while visible. The pipeline, revenue and performance screens share it.
 */
export function useWorkspaceLeads(enabled: boolean, revision = 0) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const loaded = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    if (!loaded.current) setLoading(true);
    (async () => {
      try {
        const rows: Lead[] = [];
        let offset = 0;
        let count = 0;
        for (;;) {
          const page = await listLeads({ limit: PAGE, offset }, controller.signal);
          rows.push(...page.items);
          count = page.total;
          offset += page.items.length;
          if (!page.has_more || offset >= MAX_LEADS || page.items.length === 0) break;
        }
        if (controller.signal.aborted) return;
        setLeads(rows);
        setTotal(count);
        setError(null);
        loaded.current = true;
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Leads couldn’t be loaded.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [enabled, revision, tick]);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    }, 120_000);
    return () => window.clearInterval(timer);
  }, [enabled]);

  const reload = useCallback(() => setTick((value) => value + 1), []);
  const replace = useCallback((lead: Lead) => setLeads((current) => current.map((item) => (item.id === lead.id ? lead : item))), []);
  return { leads, total, loading, error, reload, replace, truncated: total > leads.length };
}

/** Lead id → the API's derived sales stage, from the pipeline analytics snapshot. */
export function recordedStages(pipeline: PipelineAnalytics | null | undefined) {
  const map = new Map<string, string>();
  for (const stage of pipeline?.sales_stages ?? []) {
    for (const card of stage.leads) map.set(card.id, stage.stage);
  }
  return map;
}

export function useDealViews(leads: Lead[], pipeline: PipelineAnalytics | null | undefined): DealView[] {
  const store = useDeals();
  const stages = useMemo(() => recordedStages(pipeline), [pipeline]);
  return useMemo(
    () => {
      const closed = leads.filter((lead) => ["closed_won", "closed_lost"].includes(lead.outcome?.pipeline_stage ?? ""));
      const rate = closed.length
        ? closed.filter((lead) => lead.outcome?.pipeline_stage === "closed_won").length / closed.length
        : null;
      return leads.map((lead) => viewDeal(lead, store.deals[lead.id], stages.get(lead.id), rate));
    },
    [leads, store, stages],
  );
}
