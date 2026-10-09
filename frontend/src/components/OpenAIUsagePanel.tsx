import { useEffect, useMemo, useState } from "react";
import { getReasoningUsage } from "../api";
import type { ReasoningUsage, ReasoningUsagePeriod, ReasoningUsageProvider } from "../types";
import "./OpenAIUsagePanel.css";

const periods: Array<{ value: ReasoningUsagePeriod; label: string }> = [
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annual", label: "Annual" },
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

function formatDateRange(usage: ReasoningUsage) {
  const options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: usage.timezone || "UTC",
  };
  try {
    const start = new Intl.DateTimeFormat(undefined, options).format(new Date(usage.starts_at));
    const end = new Intl.DateTimeFormat(undefined, options).format(new Date(usage.ends_at));
    return `${start} – ${end} · ${usage.timezone || "UTC"}`;
  } catch {
    return `${usage.starts_at} – ${usage.ends_at} · ${usage.timezone || "UTC"}`;
  }
}

export function OpenAIUsagePanel() {
  const [period, setPeriod] = useState<ReasoningUsagePeriod>("monthly");
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

  const providers = useMemo(() => {
    const byName = new Map((usage?.providers ?? []).map((provider) => [provider.provider, provider]));
    return providerOrder.map((provider) => byName.get(provider)).filter((provider): provider is ReasoningUsageProvider => Boolean(provider));
  }, [usage]);
  const totals = useMemo(() => providers.reduce((summary, provider) => ({
    requests: summary.requests + provider.requests,
    successful_requests: summary.successful_requests + provider.successful_requests,
    failed_requests: summary.failed_requests + provider.failed_requests,
    input_tokens: summary.input_tokens + provider.input_tokens,
    output_tokens: summary.output_tokens + provider.output_tokens,
    total_tokens: summary.total_tokens + provider.total_tokens,
  }), {
    requests: 0,
    successful_requests: 0,
    failed_requests: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
  }), [providers]);

  return <section className="openai-usage" aria-label="Workspace AI usage">
    <header className="reasoning-usage-header">
      <div className="reasoning-usage-title">
        <span className="reasoning-usage-kicker">Workspace reporting</span>
        <h2>AI usage</h2>
        <p>Aggregated requests and tokens across your connected reasoning providers.</p>
      </div>
      <button className="reasoning-usage-refresh" type="button" onClick={() => setRevision((value) => value + 1)} disabled={loading}>
        {loading ? "Refreshing…" : "Refresh"}
      </button>
    </header>

    <div className="reasoning-usage-toolbar">
      <div className="openai-usage-periods" role="group" aria-label="Shared reporting period">
        {periods.map((item) => <button
          key={item.value}
          type="button"
          aria-pressed={period === item.value}
          onClick={() => setPeriod(item.value)}
        >{item.label}</button>)}
      </div>
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
      <button type="button" onClick={() => setRevision((value) => value + 1)}>Try again</button>
    </div> : usage && <>
      <div className="reasoning-usage-summary" aria-label="Combined provider totals">
        <div className="summary-primary"><span>Requests</span><strong>{formatNumber(totals.requests)}</strong><small>{formatNumber(totals.successful_requests)} successful · {formatNumber(totals.failed_requests)} failed</small></div>
        <div><span>Total tokens</span><strong>{formatNumber(totals.total_tokens)}</strong></div>
        <div><span>Input tokens</span><strong>{formatNumber(totals.input_tokens)}</strong></div>
        <div><span>Output tokens</span><strong>{formatNumber(totals.output_tokens)}</strong></div>
      </div>

      <div className="reasoning-provider-report">
        <div className="reasoning-report-label"><span>Provider</span><span>Requests</span><span>Input</span><span>Output</span><span>Total tokens</span></div>
        {providers.map((provider, index) => <article className={`reasoning-provider-row reasoning-provider-${provider.provider}`} key={provider.provider}>
          <div className="reasoning-provider-name">
            <span className="provider-index">{String(index + 1).padStart(2, "0")}</span>
            <div><strong>{providerNames[provider.provider]}</strong><small>{provider.requests === 0 ? "No recorded usage" : `${formatNumber(provider.successful_requests)} successful · ${formatNumber(provider.failed_requests)} failed`}</small></div>
          </div>
          <span className="provider-metric provider-request-count">{formatNumber(provider.requests)}</span>
          <span className="provider-metric"><small>Input</small>{formatNumber(provider.input_tokens)}</span>
          <span className="provider-metric"><small>Output</small>{formatNumber(provider.output_tokens)}</span>
          <span className="provider-metric provider-total-count">{formatNumber(provider.total_tokens)}</span>
          {provider.requests === 0 && <p className="provider-no-usage">No recorded usage for {providerNames[provider.provider]} in this period.</p>}
          {provider.models.length > 0 && <details className="provider-model-details">
            <summary>View {provider.models.length} model{provider.models.length === 1 ? "" : "s"}</summary>
            <div className="provider-model-table-wrap">
              <table>
                <caption>{providerNames[provider.provider]} usage by model</caption>
                <thead><tr><th scope="col">Model</th><th scope="col">Requests</th><th scope="col">Successful / failed</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Total tokens</th></tr></thead>
                <tbody>{provider.models.map((model) => <tr key={model.model}>
                  <th scope="row">{model.model}</th>
                  <td>{formatNumber(model.requests)}</td>
                  <td>{formatNumber(model.successful_requests)} / {formatNumber(model.failed_requests)}</td>
                  <td>{formatNumber(model.input_tokens)}</td>
                  <td>{formatNumber(model.output_tokens)}</td>
                  <td>{formatNumber(model.total_tokens)}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </details>}
        </article>)}
      </div>
      <p className="reasoning-usage-note">Workspace aggregates only. No credentials, prompts, lead details, or raw requests are included. This is usage visibility, not account-wide billing or a charge estimate.</p>
    </>}
  </section>;
}
