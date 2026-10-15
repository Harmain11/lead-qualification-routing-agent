import { Fragment, useEffect, useMemo, useState } from "react";
import { getReasoningUsage } from "../api";
import type { ReasoningUsage, ReasoningUsagePeriod, ReasoningUsageProvider } from "../types";
import "./OpenAIUsagePanel.css";

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

  return <section className="openai-usage reasoning-usage" aria-label="Workspace AI usage">
    <header className="reasoning-usage-header">
      <div className="reasoning-usage-title">
        <span className="reasoning-usage-kicker">Workspace reporting</span>
        <h2>AI usage</h2>
        <p>Aggregated requests and tokens across your connected reasoning providers.</p>
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
      <div className="reasoning-usage-summary" aria-label="Combined provider totals">
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
      </div>

      <div className="reasoning-provider-report">
        <div className="reasoning-provider-report-heading">
          <div><h3>Provider usage</h3><p>Requests and token totals for each connected provider.</p></div>
          <span>{providers.length} {providers.length === 1 ? "provider" : "providers"}</span>
        </div>
        <div className="reasoning-provider-table-wrap">
          <table className="reasoning-provider-table">
            <caption className="sr-only">Workspace AI usage by provider</caption>
            <thead><tr>
              <th scope="col">Provider</th>
              <th scope="col">Requests</th>
              <th scope="col">Input tokens</th>
              <th scope="col">Output tokens</th>
              <th scope="col">Total tokens</th>
            </tr></thead>
            <tbody>
              {providers.length === 0 && <tr className="provider-empty-row"><td className="provider-empty-state" colSpan={5}>No provider usage is available for this period.</td></tr>}
              {providers.map((provider) => <Fragment key={provider.provider}>
                <tr className={`reasoning-provider-row reasoning-provider-${provider.provider}`}>
                  <th scope="row" className="reasoning-provider-name" data-label="Provider">
                    <strong>{providerNames[provider.provider]}</strong>
                    <small>{provider.requests === 0 ? "No recorded usage" : `${formatNumber(provider.successful_requests)} successful · ${formatNumber(provider.failed_requests)} failed`}</small>
                  </th>
                  <td className="provider-metric" data-label="Requests">{formatNumber(provider.requests)}</td>
                  <td className="provider-metric" data-label="Input tokens">{formatNumber(provider.input_tokens)}</td>
                  <td className="provider-metric" data-label="Output tokens">{formatNumber(provider.output_tokens)}</td>
                  <td className="provider-metric provider-total-count" data-label="Total tokens">{formatNumber(provider.total_tokens)}</td>
                </tr>
                {provider.requests === 0 && <tr className="provider-empty-row"><td colSpan={5}><p className="provider-no-usage">No recorded usage for {providerNames[provider.provider]} in this period.</p></td></tr>}
                {provider.models.length > 0 && <tr className="provider-model-row"><td colSpan={5}>
                  <details className="provider-model-details">
                    <summary>View {provider.models.length} model{provider.models.length === 1 ? "" : "s"}</summary>
                    <div className="provider-model-table-wrap">
                      <table>
                        <caption>{providerNames[provider.provider]} usage by model</caption>
                        <thead><tr><th scope="col">Model</th><th scope="col">Requests</th><th scope="col">Successful / failed</th><th scope="col">Input tokens</th><th scope="col">Output tokens</th><th scope="col">Total tokens</th></tr></thead>
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
                  </details>
                </td></tr>}
              </Fragment>)}
            </tbody>
          </table>
        </div>
      </div>
      <p className="reasoning-usage-note">Workspace aggregates only. No credentials, prompts, lead details, or raw requests are included. This is usage visibility, not account-wide billing or a charge estimate.</p>
    </>}
  </section>;
}
