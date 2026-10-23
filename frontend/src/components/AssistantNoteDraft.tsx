import { useId } from "react";
import "./assistantNoteDraft.css";

export type AssistantNoteDraftProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
};

export function AssistantNoteDraft({
  value,
  onChange,
  disabled = false,
  label = "Team note",
}: AssistantNoteDraftProps) {
  const inputId = useId();
  const count = value.length;

  return (
    <section className="assistant-note-draft" aria-label="Assistant note draft">
      <div className="assistant-note-draft__heading">
        <label className="assistant-note-draft__label" htmlFor={inputId}>
          {label}
        </label>
        <span className="assistant-note-draft__badge">Draft</span>
      </div>
      <textarea
        id={inputId}
        className="assistant-note-draft__textarea"
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        disabled={disabled}
        maxLength={5000}
        rows={6}
        aria-describedby={`${inputId}-guidance ${inputId}-count`}
      />
      <div className="assistant-note-draft__footer">
        <p className="assistant-note-draft__guidance" id={`${inputId}-guidance`}>
          Nothing is saved until you confirm. Comments do not change sales status.
        </p>
        <p className="assistant-note-draft__count" id={`${inputId}-count`} aria-live="polite">
          {count.toLocaleString()} / 5,000
        </p>
      </div>
    </section>
  );
}
