import { useEffect, useMemo, useState } from "react";
import { getReasoningUsage } from "../api";
import type { ReasoningUsage, ReasoningUsagePeriod, ReasoningUsageProvider } from "../types";
import { estimateModelCostUsd } from "./aiUsagePricing";

type ModelUsageRow = {
  provider: ReasoningUsageProvider["provider"];
  model: ReasoningUsageProvider["models"][number];
  key: string;
};

const ALL_MODELS = "all";

const periods: Array<{ value: ReasoningUsagePeriod; label: string }> = [
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annual", label: "Annually" },
];

const providerOrder: ReasoningUsageProvider["provider"][] = [
  "openai", "gemini", "claude", "deepseek", "xai",
];
const providerNames: Record<ReasoningUsageProvider["provider"], string> = {
  openai: "OpenAI",
  gemini: "Gemini",
  claude: "Claude",
  deepseek: "DeepSeek",
  xai: "xAI",
};

function formatNumber(value: number) {
  return Number(value || 0).toLocaleString();
}

function formatUsd(value: number | null) {
  if (value === null) return "—";
  const fractionDigits = value > 0 && value < 0.01 ? 4 : 2;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: 4,
  }).format(value);
}

function formatDateRange(usage: ReasoningUsage) {
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  };
  try {
    const start = new Intl.DateTimeFormat(undefined, options).format(new Date(usage.starts_at));
    const end = new Intl.DateTimeFormat(undefined, options).format(new Date(usage.ends_at));
    return `${start} – ${end} · UTC`;
  } catch {
    return `${usage.starts_at} – ${usage.ends_at} · UTC`;
  }
}

export function OpenAIUsagePanel() {
  const [period, setPeriod] = useState<ReasoningUsagePeriod>("monthly");
  const [selectedModel, setSelectedModel] = useState(ALL_MODELS);
  const [usage, setUsage] = useState<ReasoningUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setUsage(null);
    void getReasoningUsage(period, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setUsage(data);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Workspace usage could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [period, revision]);

  const modelOptions = useMemo(() => {
    const byName = new Map((usage?.providers ?? []).map((provider) => [provider.provider, provider]));
    return providerOrder.flatMap((provider) => {
      const providerUsage = byName.get(provider);
      return (providerUsage?.models ?? [])
        .filter((model) => model.requests > 0 || model.total_tokens > 0)
        .map((model): ModelUsageRow => ({
          provider,
          model,
          key: JSON.stringify([provider, model.model]),
        }));
    });
  }, [usage]);
  useEffect(() => {
    if (usage && selectedModel !== ALL_MODELS && !modelOptions.some((item) => item.key === selectedModel)) {
      setSelectedModel(ALL_MODELS);
    }
  }, [modelOptions, selectedModel, usage]);

  const visibleModels = useMemo(
    () => selectedModel === ALL_MODELS
      ? modelOptions
      : modelOptions.filter((item) => item.key === selectedModel),
    [modelOptions, selectedModel],
  );
  const totals = useMemo(() => visibleModels.reduce((summary, item) => ({
    requests: summary.requests + item.model.requests,
    successful_requests: summary.successful_requests + item.model.successful_requests,
    failed_requests: summary.failed_requests + item.model.failed_requests,
    input_tokens: summary.input_tokens + item.model.input_tokens,
    output_tokens: summary.output_tokens + item.model.output_tokens,
    total_tokens: summary.total_tokens + item.model.total_tokens,
  }), {
    requests: 0,
    successful_requests: 0,
    failed_requests: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
  }), [visibleModels]);
  const modelCosts = useMemo(() => visibleModels.map((item) => ({
    key: item.key,
    cost: estimateModelCostUsd(
      item.provider,
      item.model.model,
      item.model.input_tokens,
      item.model.output_tokens,
    ),
  })), [visibleModels]);
  const modelCostByKey = useMemo(
    () => new Map(modelCosts.map((item) => [item.key, item.cost])),
    [modelCosts],
  );
  const estimatedTotalCost = modelCosts.length === 0 || modelCosts.some((item) => item.cost === null)
    ? null
    : modelCosts.reduce((total, item) => total + (item.cost ?? 0), 0);

  return <section className="openai-usage reasoning-usage" aria-label="Workspace AI usage">
    <header className="reasoning-usage-header">
      <div className="reasoning-usage-title">
        <span className="reasoning-usage-kicker">Workspace reporting</span>
        <h2>AI usage</h2>
        <p>Usage is shown for AI models that processed requests in the selected period.</p>
      </div>
      <button className="button-quiet reasoning-usage-refresh" type="button" onClick={() => setRevision((value) => value + 1)} disabled={loading}>
        {loading ? "Refreshing…" : "Refresh"}
      </button>
    </header>

    <div className="reasoning-usage-toolbar">
      <div className="openai-usage-periods reasoning-usage-periods" role="group" aria-label="Reporting period">
        {periods.map((item) => <button
          key={item.value}
          type="button"
          aria-pressed={period === item.value}
          onClick={() => setPeriod(item.value)}
        >{item.label}</button>)}
      </div>
      {usage && <label className="reasoning-usage-model-filter">
        <span>AI model</span>
        <select
          aria-label="Filter usage by AI model"
          value={selectedModel}
          onChange={(event) => setSelectedModel(event.target.value)}
          disabled={modelOptions.length === 0}
        >
          <option value={ALL_MODELS}>All models</option>
          {modelOptions.map((item) => (
            <option key={item.key} value={item.key}>
              {providerNames[item.provider]} · {item.model.model}
            </option>
          ))}
        </select>
      </label>}
      {usage && <p className="reasoning-usage-range"><span className="range-mark" aria-hidden="true" />{formatDateRange(usage)}</p>}
    </div>

    {loading ? <div className="reasoning-usage-loading" role="status" aria-label="Loading workspace AI usage">
      <div className="usage-skeleton usage-skeleton-summary" />
      <div className="usage-skeleton usage-skeleton-row" />
      <div className="usage-skeleton usage-skeleton-row" />
      <div className="usage-skeleton usage-skeleton-row" />
      <span>Loading shared workspace report</span>
    </div> : error ? <div className="reasoning-usage-error" role="alert">
      <div><strong>Usage report unavailable</strong><p>{error}</p></div>
      <button className="button-quiet" type="button" onClick={() => setRevision((value) => value + 1)}>Try again</button>
    </div> : usage && <>
      <div className="reasoning-usage-summary" aria-label="Combined model totals">
        <article className="reasoning-usage-metric summary-primary">
          <span>Requests</span>
          <strong>{formatNumber(totals.requests)}</strong>
          <div className="reasoning-request-outcomes">
            <span><b>{formatNumber(totals.successful_requests)}</b> successful</span>
            <span><b>{formatNumber(totals.failed_requests)}</b> failed</span>
          </div>
        </article>
        <article className="reasoning-usage-metric"><span>Total tokens</span><strong>{formatNumber(totals.total_tokens)}</strong></article>
        <article className="reasoning-usage-metric"><span>Input tokens</span><strong>{formatNumber(totals.input_tokens)}</strong></article>
        <article className="reasoning-usage-metric"><span>Output tokens</span><strong>{formatNumber(totals.output_tokens)}</strong></article>
        <article className="reasoning-usage-metric estimated-cost-metric">
          <span>Estimated cost</span>
          <strong title={estimatedTotalCost === null ? "Pricing unavailable for one or more models." : "Estimate based on recorded tokens and configured published rates."}>{formatUsd(estimatedTotalCost)}</strong>
        </article>
      </div>

      <div className="reasoning-provider-report">
        <div className="reasoning-provider-report-heading">
          <div><h3>AI model usage</h3><p>Unused providers and models are hidden.</p></div>
          <span>{visibleModels.length} {visibleModels.length === 1 ? "model" : "models"}</span>
        </div>
        <div className="reasoning-provider-table-wrap">
          <table className="reasoning-provider-table">
            <caption className="sr-only">Workspace AI usage by model</caption>
            <thead><tr>
              <th scope="col">AI model</th>
              <th scope="col">Provider</th>
              <th scope="col">Requests</th>
              <th scope="col">Input tokens</th>
              <th scope="col">Output tokens</th>
              <th scope="col">Total tokens</th>
              <th scope="col">Estimated cost</th>
            </tr></thead>
            <tbody>
              {visibleModels.length === 0 && <tr className="provider-empty-row"><td className="provider-empty-state" colSpan={7}>No AI model usage was recorded in this period.</td></tr>}
              {visibleModels.map(({ provider, model, key }) => {
                const cost = modelCostByKey.get(key) ?? null;
                return <tr className="reasoning-provider-row" key={key}>
                  <th scope="row" className="reasoning-model-name" data-label="AI model">
                    <strong>{model.model}</strong>
                    <small>{formatNumber(model.successful_requests)} successful · {formatNumber(model.failed_requests)} failed</small>
                  </th>
                  <td className="reasoning-model-provider" data-label="Provider">{providerNames[provider]}</td>
                  <td className="provider-metric" data-label="Requests">{formatNumber(model.requests)}</td>
                  <td className="provider-metric" data-label="Input tokens">{formatNumber(model.input_tokens)}</td>
                  <td className="provider-metric" data-label="Output tokens">{formatNumber(model.output_tokens)}</td>
                  <td className="provider-metric provider-total-count" data-label="Total tokens">{formatNumber(model.total_tokens)}</td>
                  <td className="provider-metric estimated-cost-cell" data-label="Estimated cost" title={cost === null ? "Pricing unavailable for this model." : undefined}>{formatUsd(cost)}</td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>
      </div>
      <p className="reasoning-usage-note">Workspace aggregates only; no credentials, prompts, lead details, or raw requests are included. Estimated cost uses configured published per-token rates and recorded input/output tokens, and may differ from actual billing due to caching, discounts, or provider adjustments. Provider invoices are authoritative. “—” means pricing is unavailable for one or more models.</p>
    </>}
  </section>;
}
