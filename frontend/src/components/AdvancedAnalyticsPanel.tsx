import { useEffect, useState } from "react";
import { opsGet, opsSave, type AdvancedAnalytics } from "../operationsApi";
const amount = (value: number | null) =>
  value === null
    ? "Insufficient data"
    : new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
export function AdvancedAnalyticsPanel() {
  const [data, setData] = useState<AdvancedAnalytics | null>(null);
  const [months, setMonths] = useState(6);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<AdvancedAnalytics>(
      `/v1/intelligence/analytics?months=${months}`,
      controller.signal,
    )
      .then(setData)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [months]);
  return (
    <section className="ops-page">
      <div className="ops-toolbar">
        <h2>Forecasts, ROI & cohorts</h2>
        <label>
          Observation window
          <select
            value={months}
            onChange={(event) => setMonths(Number(event.target.value))}
          >
            <option value={3}>3 months</option>
            <option value={6}>6 months</option>
            <option value={12}>12 months</option>
            <option value={24}>24 months</option>
          </select>
        </label>
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setError("");
            void opsSave<AdvancedAnalytics>(
              `/v1/intelligence/analytics/narrate?months=${months}`,
              {},
            )
              .then(setData)
              .catch((reason) => setError(reason.message))
              .finally(() => setBusy(false));
          }}
        >
          Narrate recorded forecast
        </button>
      </div>
      {error && (
        <p role="alert" className="team-alert">
          {error}
        </p>
      )}
      {!data ? (
        <p role="status">Loading analytics…</p>
      ) : (
        <>
          <div className="ops-panel">
            <h3>{data.summary_method}</h3>
            <p>{data.summary}</p>
            <p className="ops-muted">{data.forecast.scope}</p>
            <div className="ops-metrics">
              <span>
                <b>{amount(data.forecast.closed_usd_this_month)}</b>Closed this
                month
              </span>
              <span>
                <b>{amount(data.forecast.open_pipeline_usd)}</b>Recorded open
                pipeline
              </span>
              <span>
                <b>{amount(data.forecast.expected_open_pipeline_usd)}</b>
                Expected pipeline value
              </span>
              <span>
                <b>
                  {data.forecast.coverage !== null
                    ? data.forecast.coverage + "×"
                    : "No quota set"}
                </b>
                Pipeline coverage
              </span>
            </div>
            <p className="ops-muted">
              {data.forecast.method} {data.forecast.unvalued_open_leads} open
              leads have no recorded value.
            </p>
          </div>
          <div className="ops-panel">
            <h3>Channel ROI</h3>
            <p>
              Costs and revenue use intake-source/month attribution. Unknown
              costs remain unknown.
            </p>
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Source / month</th>
                    <th>Leads / qualified</th>
                    <th>Wins</th>
                    <th>Cost</th>
                    <th>Closed revenue</th>
                    <th>Cost / qualified</th>
                    <th>ROI</th>
                  </tr>
                </thead>
                <tbody>
                  {data.channels.map((row) => (
                    <tr key={row.source + row.month}>
                      <td>
                        {row.source} / {row.month}
                      </td>
                      <td>
                        {row.leads} / {row.qualified}
                      </td>
                      <td>{row.won}</td>
                      <td>
                        {row.cost_usd === null
                          ? "Unknown"
                          : amount(row.cost_usd)}
                      </td>
                      <td>{amount(row.closed_usd)}</td>
                      <td>
                        {row.cost_per_qualified === null
                          ? "—"
                          : amount(row.cost_per_qualified)}
                      </td>
                      <td>
                        {row.roi_percent === null ? "—" : row.roi_percent + "%"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="ops-panel">
            <h3>Member comparison with context</h3>
            <p className="ops-muted">{data.fairness_method}</p>
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Member</th>
                    <th>Closed / wins</th>
                    <th>Comparable leads</th>
                    <th>Expected wins</th>
                    <th>Adjusted win index</th>
                  </tr>
                </thead>
                <tbody>
                  {data.members.map((row) => (
                    <tr key={row.user_id}>
                      <td>{row.name}</td>
                      <td>
                        {row.closed} / {row.wins}
                      </td>
                      <td>{row.comparable_leads}</td>
                      <td>{row.expected_wins}</td>
                      <td>
                        {row.adjusted_win_index === null
                          ? "Insufficient data"
                          : row.adjusted_win_index + "×"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="ops-panel">
            <h3>Intake cohorts to close</h3>
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Intake week</th>
                    <th>Campaign</th>
                    <th>Leads</th>
                    <th>Qualified</th>
                    <th>Won</th>
                    <th>Lost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.cohorts.map((row) => (
                    <tr key={row.week + row.campaign}>
                      <td>{row.week}</td>
                      <td>{row.campaign}</td>
                      <td>{row.leads}</td>
                      <td>{row.qualified}</td>
                      <td>{row.won}</td>
                      <td>{row.lost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {data.truncated && (
            <p className="ops-muted">
              Analysis is limited to the latest 5,000 leads in this window.
            </p>
          )}
        </>
      )}
    </section>
  );
}
