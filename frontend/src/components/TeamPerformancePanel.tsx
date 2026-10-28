import { useEffect, useState } from "react";
import type { RepAnalytics, TeamMemberPerformance } from "../types";
import "./teamPerformance.css";

const money = (value: number) => new Intl.NumberFormat(undefined, {
  style: "currency", currency: "USD", maximumFractionDigits: 2,
}).format(value);

export function TeamPerformancePanel({ data }: { data: RepAnalytics }) {
  const members = data.members ?? [];
  const [selected, setSelected] = useState<string[]>([]);
  const [profileId, setProfileId] = useState("");
  const rosterKey = members.map((member) => member.user_id).join(",");
  useEffect(() => {
    setSelected((current) => {
      const valid = current.filter((id) => members.some((member) => member.user_id === id));
      return valid.length ? valid : members.slice(0, 4).map((member) => member.user_id);
    });
    setProfileId((current) => members.some((member) => member.user_id === current) ? current : members[0]?.user_id ?? "");
  }, [rosterKey]);
  const comparison = members.filter((member) => selected.includes(member.user_id));
  const profile = members.find((member) => member.user_id === profileId);
  const metrics: Array<[string, (member: TeamMemberPerformance) => string | number]> = [
    ["Assigned leads", (member) => member.assigned_leads],
    ["Qualified leads", (member) => member.qualified_leads],
    ["Open leads", (member) => member.active_leads],
    ["Projects won", (member) => member.project_wins],
    ["Sales won", (member) => member.sale_wins],
    ["All wins", (member) => member.closed_leads],
    ["Lost", (member) => member.lost_leads],
    ["Assigned-lead conversion", (member) => `${member.conversion_rate}%`],
    ["Known revenue (USD)", (member) => money(member.revenue)],
    ["Wins without a value", (member) => member.wins_without_value],
  ];
  return <section className="panel team-performance-panel">
    <div className="section-heading"><div><span className="eyebrow">Member-level analytics</span><h2>Team performance</h2></div></div>
    <p className="performance-caption">{data.cohort_description ?? "Compare workspace members using recorded lead assignments and outcomes."}</p>
    {members.length ? <>
      <fieldset className="performance-member-picker">
        <legend>Choose members to compare</legend>
        {members.map((member) => <label key={member.user_id}>
          <input type="checkbox" checked={selected.includes(member.user_id)} onChange={(event) => {
            setSelected((current) => event.target.checked ? [...current, member.user_id] : current.filter((id) => id !== member.user_id));
          }} />
          <span>{member.display_name}<small>{member.role.replaceAll("_", " ")}{member.status !== "active" ? ` · ${member.status}` : ""}</small></span>
        </label>)}
      </fieldset>
      {comparison.length >= 2 ? <div className="performance-table-scroll">
        <table className="performance-comparison"><caption>Selected team-member comparison</caption>
          <thead><tr><th scope="col">Metric</th>{comparison.map((member) => <th scope="col" key={member.user_id}>{member.display_name}</th>)}</tr></thead>
          <tbody>{metrics.map(([label, value]) => <tr key={label}><th scope="row">{label}</th>{comparison.map((member) => <td key={member.user_id}>{value(member)}</td>)}</tr>)}</tbody>
        </table>
      </div> : <p className="performance-empty">Select at least two members for a side-by-side comparison. Individual summaries are available below.</p>}
      <div className="performance-profile-heading"><h3>Individual performance summary</h3><label>Team member
        <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>{members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}</select>
      </label></div>
      {profile && <article className="performance-profile">
        <h4>{profile.display_name}</h4><p>{profile.summary}</p>
        <dl><div><dt>Projects won</dt><dd>{profile.project_wins}</dd></div><div><dt>Sales won</dt><dd>{profile.sale_wins}</dd></div><div><dt>Conversion</dt><dd>{profile.conversion_rate}%</dd></div><div><dt>Known revenue</dt><dd>{money(profile.revenue)}</dd></div></dl>
        {profile.untyped_wins > 0 && <small>{profile.untyped_wins} earlier wins have no Project/Sale type.</small>}
        {profile.wins_without_value > 0 && <small>{profile.wins_without_value} wins have no recorded value and are not included in revenue.</small>}
      </article>}
    </> : <p className="performance-empty">No workspace members are available for comparison.</p>}
    {!!data.unattributed_leads && <p className="performance-caption">{data.unattributed_leads} leads are not linked to a workspace member and are excluded from member comparisons. Choose a credited member when recording a win.</p>}
    {!members.length && data.reps.length > 0 && <details className="performance-legacy"><summary>Legacy routing representatives (not member accounts)</summary>
      {data.reps.map((rep) => <p key={rep.rep_name}><b>{rep.rep_name}</b> · {rep.assigned_leads} leads · {rep.closed_leads} wins · {money(rep.revenue)}</p>)}
    </details>}
  </section>;
}
