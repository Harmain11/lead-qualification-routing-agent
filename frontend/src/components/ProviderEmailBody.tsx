import { splitGmailBodyForDisplay } from "../gmailInquiry";
import "./providerEmailBody.css";

export function getProviderEmailPreview(content: string, maxLength = 140) {
  const primary = splitGmailBodyForDisplay(content).primary.replace(/\s+/g, " ").trim();
  if (!primary) return "Open to review message details.";
  const limit = Math.max(1, maxLength);
  return primary.length > limit
    ? `${primary.slice(0, limit - 1).trimEnd()}…`
    : primary;
}

export function ProviderEmailBody({ content }: { content: string }) {
  const sections = splitGmailBodyForDisplay(content);
  const hasSupportingContent = sections.quoted.length > 0 || sections.footers.length > 0;

  return <div className="provider-email-body">
    {sections.primary
      ? <div className="provider-email-body-main">{sections.primary}</div>
      : !hasSupportingContent && <p className="provider-email-body-empty">No message text was included.</p>}
    {sections.quoted.length > 0 && <details className="provider-email-disclosure">
      <summary>Show quoted or forwarded content ({sections.quoted.length})</summary>
      <div className="provider-email-disclosure-content">
        {sections.quoted.map((section, index) => (
          <div className="provider-email-body-main" key={`quoted-${index}`}>{section}</div>
        ))}
      </div>
    </details>}
    {sections.footers.length > 0 && <details className="provider-email-disclosure">
      <summary>Show security notices and email footer ({sections.footers.length})</summary>
      <div className="provider-email-disclosure-content">
        {sections.footers.map((section, index) => (
          <div className="provider-email-body-main" key={`footer-${index}`}>{section}</div>
        ))}
      </div>
    </details>}
  </div>;
}
