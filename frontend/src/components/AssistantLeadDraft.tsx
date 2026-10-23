import { useId } from "react";
import { AssistantNoteDraft } from "./AssistantNoteDraft";
import "./assistantLeadDraft.css";

export type AssistantLeadDraftProps = {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  disabled?: boolean;
};

type LeadDraftField = {
  key: string;
  label: string;
  kind: "text" | "number" | "textarea";
  placeholder?: string;
  step?: string;
  wide?: boolean;
};

const leadDraftFields: LeadDraftField[] = [
  { key: "full_name", label: "Full name", kind: "text", placeholder: "Name as provided" },
  { key: "email", label: "Email", kind: "text", placeholder: "Email address" },
  { key: "phone", label: "Phone", kind: "text", placeholder: "Phone number" },
  { key: "company_name", label: "Company", kind: "text", placeholder: "Company name" },
  { key: "job_title", label: "Job title", kind: "text", placeholder: "Role or title" },
  { key: "product_or_service", label: "Product or service", kind: "text", placeholder: "What they are looking for" },
  { key: "industry", label: "Industry", kind: "text", placeholder: "Industry" },
  { key: "geography", label: "Geography", kind: "text", placeholder: "Location or region" },
  { key: "employee_count", label: "Employee count", kind: "number", step: "1" },
  { key: "annual_revenue", label: "Annual revenue", kind: "number", step: "any" },
  { key: "budget", label: "Budget", kind: "number", step: "any" },
  { key: "message", label: "Prospect message", kind: "textarea", placeholder: "What the prospect said or asked for", wide: true },
];

export function AssistantLeadDraft({
  value,
  onChange,
  disabled = false,
}: AssistantLeadDraftProps) {
  const idPrefix = useId();
  const followUpNote = typeof value.follow_up_note === "string" ? value.follow_up_note : "";

  function updateField(key: string, nextValue: string | number | null) {
    onChange({ ...value, [key]: nextValue });
  }

  return (
    <section className="assistant-lead-draft" aria-label="Assistant lead draft">
      <header className="assistant-lead-draft__header">
        <div className="assistant-lead-draft__intro">
          <h3>Review lead details</h3>
          <p>Check the details before approval. Nothing is created until you approve this draft.</p>
        </div>
        <span className="assistant-lead-draft__badge">Draft</span>
      </header>

      <div className="assistant-lead-draft__fields">
        {leadDraftFields.map((field) => {
          const fieldId = `${idPrefix}-${field.key}`;
          const fieldClass = [
            "assistant-lead-draft__field",
            field.wide ? "assistant-lead-draft__field--wide" : "",
          ].filter(Boolean).join(" ");

          if (field.kind === "textarea") {
            return (
              <label className={fieldClass} htmlFor={fieldId} key={field.key}>
                {field.label}
                <textarea
                  id={fieldId}
                  value={typeof value[field.key] === "string" ? value[field.key] as string : ""}
                  onChange={(event) => updateField(field.key, event.currentTarget.value)}
                  placeholder={field.placeholder}
                  disabled={disabled}
                  rows={4}
                />
              </label>
            );
          }

          if (field.kind === "number") {
            const rawValue = value[field.key];
            const numericValue = typeof rawValue === "number" && Number.isFinite(rawValue) && rawValue >= 0
              ? String(rawValue)
              : "";

            return (
              <label className={fieldClass} htmlFor={fieldId} key={field.key}>
                {field.label}
                <input
                  id={fieldId}
                  type="number"
                  min="0"
                  step={field.step}
                  value={numericValue}
                  onChange={(event) => {
                    const text = event.currentTarget.value;
                    if (!text) {
                      updateField(field.key, null);
                      return;
                    }
                    const parsed = Number(text);
                    if (Number.isFinite(parsed) && parsed >= 0) updateField(field.key, parsed);
                  }}
                  disabled={disabled}
                />
              </label>
            );
          }

          return (
            <label className={fieldClass} htmlFor={fieldId} key={field.key}>
              {field.label}
              <input
                id={fieldId}
                type="text"
                value={typeof value[field.key] === "string" ? value[field.key] as string : ""}
                onChange={(event) => updateField(field.key, event.currentTarget.value)}
                placeholder={field.placeholder}
                disabled={disabled}
              />
            </label>
          );
        })}
      </div>
      <p className="assistant-lead-draft__section-note">
        Leave unknown details blank; text is kept exactly as entered.
      </p>

      <div className="assistant-lead-draft__follow-up">
        <AssistantNoteDraft
          label="Follow-up note"
          value={followUpNote}
          onChange={(nextValue) => updateField("follow_up_note", nextValue)}
          disabled={disabled}
        />
        <p>
          This stays separate from the lead draft and unsaved after creation. Review it on the new lead before saving.
        </p>
      </div>
    </section>
  );
}
