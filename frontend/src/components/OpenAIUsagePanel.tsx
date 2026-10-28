import { useEffect, useState } from "react";
import { getOpenAIUsage } from "../api";
import type { OpenAIUsage, OpenAIUsagePeriod } from "../types";
import "./OpenAIUsagePanel.css";

const periods: Array<{ value: OpenAIUsagePeriod; label: string; days: number }> = [
  { value: "weekly", label: "Weekly", days: 7 },
  { value: "monthly", label: "Monthly", days: 30 },
  { value: "quarterly", label: "Quarterly", days: 90 },
  { value: "annual", label: "Annually", days: 365 },
];

export function OpenAIUsagePanel() {
  const [period, setPeriod] = useState<OpenAIUsagePeriod>("monthly");
  const [usage, setUsage] = useState<OpenAIUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setUsage(null);
    void getOpenAIUsage(period, controller.signal).then((data) => {
      if (!controller.signal.aborted) setUsage(data);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Usage could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [period, revision]);
  const selected = periods.find((item) => item.value === period)!;
  return <section className="openai-usage" aria-label="OpenAI API usage">
    <div className="openai-usage-heading"><strong>API usage</strong><button type="button" onClick={() => setRevision((value) => value + 1)} aria-label="Refresh OpenAI usage">Refresh</button></div>
    <div className="openai-usage-periods" aria-label="Usage period">
      {periods.map((item) => <button key={item.value} aria-pressed={period === item.value} onClick={() => setPeriod(item.value)}>{item.label}</button>)}
    </div>
    <small>Last {selected.days} days · UTC</small>
    {loading ? <p role="status">Loading usage…</p> : error ? <p role="alert">{error}</p> : usage && <>
      <div className="openai-usage-totals">
        <div><span>Requests</span><strong>{usage.requests.toLocaleString()}</strong></div>
        <div><span>Input tokens</span><strong>{usage.input_tokens.toLocaleString()}</strong></div>
        <div><span>Output tokens</span><strong>{usage.output_tokens.toLocaleString()}</strong></div>
        <div><span>Total tokens</span><strong>{usage.total_tokens.toLocaleString()}</strong></div>
      </div>
      <small>{usage.successful_requests.toLocaleString()} successful · {usage.failed_requests.toLocaleString()} unsuccessful</small>
      {usage.models.length > 0 && <div className="openai-model-usage"><table><caption>Usage by model</caption><thead><tr><th>Model</th><th>Requests</th><th>Tokens</th></tr></thead><tbody>{usage.models.map((model) => <tr key={model.model}><td>{model.model}</td><td>{model.requests.toLocaleString()}</td><td>{model.total_tokens.toLocaleString()}</td></tr>)}</tbody></table></div>}
      {usage.requests === 0 && <p>No recorded calls in this period. New workspace AI requests will appear here.</p>}
    </>}
    <p className="openai-usage-note">Corral records requests and tokens from new workspace AI calls. Older calls and account-wide billing cannot be reconstructed here. Final charges remain in your OpenAI account.</p>
  </section>;
}
