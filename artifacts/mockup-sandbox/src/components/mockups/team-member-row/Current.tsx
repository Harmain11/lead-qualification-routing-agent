import "./_group.css";
import "./current.css";

function MemberStatus({ status }: { status: string }) {
  const label = status.charAt(0).toUpperCase() + status.slice(1);
  return <span className={`team-status status-${status}`}><i />{label}</span>;
}

function RolePill({ role }: { role: string }) {
  const label = role.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  return <span className={`team-role role-${role}`}>{label}</span>;
}

export function Current() {
  return (
    <main className="team-preview-stage app-shell">
      <section className="team-preview-panel">
        <header className="team-preview-heading">
          <div>
            <span className="team-preview-eyebrow">People with access</span>
            <h2>Workspace users</h2>
          </div>
          <button className="team-preview-invite" type="button">+ Invite user</button>
        </header>
        <div className="team-member-list">
          <div className="team-member-row">
            <span className="member-avatar">J</span>
            <span className="member-identity">
              <b>Junaid Paracha<em>You</em></b>
              <small>junaid@corral.team</small>
            </span>
            <RolePill role="owner" />
            <MemberStatus status="active" />
            <span className="member-permission-count">22 permissions</span>
            <span className="member-row-actions">
              <button type="button" className="button-quiet">Edit</button>
            </span>
            <i className="member-chevron">→</i>
          </div>
        </div>
      </section>
    </main>
  );
}
