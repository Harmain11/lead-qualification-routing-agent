import { useId, useState } from "react";
import type { WorkspaceAssistantLeadLink } from "../api";

type AgentLeadChoicesProps = {
  leads: WorkspaceAssistantLeadLink[];
  onSelect: (leadId: string) => void;
};

export function AgentLeadChoices({ leads, onSelect }: AgentLeadChoicesProps) {
  const headingId = useId();
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);

  if (!leads.length) return null;

  return (
    <section className="corral-agent-choice-group" aria-labelledby={headingId}>
      <h3 className="corral-agent-choice-heading" id={headingId}>
        Choose a lead
      </h3>
      <div
        className="corral-agent-choice-list"
        role="group"
        aria-labelledby={headingId}
      >
        {leads.map((lead) => {
          const selected = selectedLeadId === lead.id;
          const description = [
            lead.company,
            lead.suggested ? "Suggested match" : null,
          ].filter(Boolean).join(" · ");
          return (
            <button
              className="corral-agent-choice"
              key={lead.id}
              type="button"
              aria-pressed={selected}
              aria-label={`Choose ${lead.name}${description ? `, ${description}` : ""}`}
              onClick={() => {
                setSelectedLeadId(lead.id);
                onSelect(lead.id);
              }}
            >
              <span className="corral-agent-choice-copy">
                <strong>{lead.name}</strong>
                <span>{description || "Select this lead"}</span>
              </span>
              <span className="corral-agent-choice-indicator" aria-hidden="true">
                {selected ? "✓" : "›"}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
