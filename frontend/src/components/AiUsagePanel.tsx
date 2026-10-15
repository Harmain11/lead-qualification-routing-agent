import { useState } from "react";
import "./OpenAIUsagePanel.css";

const periods = [
  { value: "weekly", label: "Weekly", days: 7 },
  { value: "monthly", label: "Monthly", days: 30 },
  { value: "quarterly", label: "Quarterly", days: 90 },
  { value: "annual", label: "Annually", days: 365 },
] as const;

export function AiUsagePanel({ providerName }: { providerName: string }) {
  const [period, setPeriod] = useState<(typeof periods)[number]["value"]>("monthly");
  const selected = periods.find((item) => item.value === period)!;
  return <section className="openai-usage ai-provider-usage" aria-label={`${providerName} API usage`}>
    <div className="openai-usage-heading"><strong>API usage · {providerName}</strong><span className="ai-usage-unavailable">Pending adapter</span></div>
    <div className="openai-usage-periods" role="group" aria-label={`${providerName} usage period`}>
      {periods.map((item) => <button key={item.value} type="button" aria-pressed={period === item.value} onClick={() => setPeriod(item.value)}>{item.label}</button>)}
    </div>
    <small>Last {selected.days} days · UTC</small>
    <div className="openai-usage-totals">
      <div><span>Requests</span><strong>—</strong></div>
      <div><span>Total tokens</span><strong>—</strong></div>
      <div><span>Input tokens</span><strong>—</strong></div>
      <div><span>Output tokens</span><strong>—</strong></div>
    </div>
    <p className="openai-usage-note">Corral will show {providerName} requests and token totals here after its analysis adapter is enabled. Account-level billing and usage remain available in the provider console.</p>
  </section>;
}
