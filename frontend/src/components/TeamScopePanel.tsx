import { useEffect, useState } from "react";
import { opsGet, opsSave } from "../operationsApi";
import type { WorkspaceMember } from "../types";
import { LeadPicker } from "./LeadPicker";

type Team = { id: string; name: string; description: string; member_ids: string[]; lead_ids: string[]; is_default: boolean };

export function TeamScopePanel({ members, canManage }: { members: WorkspaceMember[]; canManage: boolean }) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [picker, setPicker] = useState<string | null>(null);
  const [chosen, setChosen] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    opsGet<Team[]>("/v1/teams", controller.signal).then(setTeams).catch((reason) => {
      if (!controller.signal.aborted) setError(String(reason.message || reason));
    });
    return () => controller.abort();
  }, []);
  async function save(path: string, data: unknown, method = "PUT") {
    setBusy(true); setError("");
    try {
      await opsSave(path, data, method);
      setTeams(await opsGet<Team[]>("/v1/teams"));
      setName("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Team access could not be saved."); }
    finally { setBusy(false); }
  }
  return <section className="panel" style={{ padding: 20 }} aria-label="Team visibility">
    <h2>Team visibility</h2>
    <p>Members share leads linked to their teams. Removing a team link removes that team's access; other team links remain active.</p>
    {error && <p role="alert">{error}</p>}
    {canManage && <form onSubmit={(event) => { event.preventDefault(); void save("/v1/teams", { name: name.trim() }, "POST"); }}>
      <label className="team-field"><span>New team name</span><input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required /></label>
      <button className="secondary-button" disabled={busy || !name.trim()}>Create team</button>
    </form>}
    {!teams.length && !error && <p>No teams are available to your account.</p>}
    {teams.map((team) => <details key={team.id} style={{ marginTop: 16 }}>
      <summary>{team.name} · {team.member_ids.length} members · {team.lead_ids.length} leads</summary>
      <p>{team.description}</p>
      {members.filter((member) => member.status === "active").map((member) => <label key={member.id} style={{ display: "flex", gap: 8, padding: 8 }}>
        <input type="checkbox" checked={team.member_ids.includes(member.id)} disabled={!canManage || busy}
          onChange={(event) => void save(`/v1/teams/${team.id}/members/${member.id}`, {}, event.target.checked ? "PUT" : "DELETE")} />
        <span>{member.display_name}<small style={{ display: "block", color: "var(--text-muted, #667085)" }}>{member.slack_member_id ? `Slack ${member.slack_member_id}` : "Slack not linked"}</small></span>
      </label>)}
      {canManage && <>
        <button className="secondary-button" disabled={busy} onClick={() => { setChosen(null); setPicker(picker === team.id ? null : team.id); }}>{picker === team.id ? "Close lead picker" : "Manage lead access"}</button>
        {picker === team.id && <LeadPicker placeholder="Search a lead to manage team access" onPick={(lead) => setChosen({ id: lead.id, name: lead.full_name || lead.company_name })} />}
        {picker === team.id && chosen && <p>{chosen.name} <button className="secondary-button" disabled={busy} onClick={() => void save(`/v1/teams/${team.id}/leads/${chosen.id}`, {}, team.lead_ids.includes(chosen.id) ? "DELETE" : "PUT")}>{busy ? "Saving…" : team.lead_ids.includes(chosen.id) ? "Remove team access" : "Add team access"}</button></p>}
      </>}
    </details>)}
  </section>;
}
