import { splitGmailBodyForDisplay } from "../gmailInquiry";
import "./providerEmailBody.css";

const WEB_URL = /https?:\/\/[^\s<>\]\["']+/gi;

function compactUrlLabel(value: string) {
  try {
    const url = new URL(value);
    const path = url.pathname === "/" ? "" : url.pathname.length > 26 ? `${url.pathname.slice(0, 25)}…` : url.pathname;
    return `${url.hostname.replace(/^www\./, "")}${path}`;
  } catch {
    return value.length > 48 ? `${value.slice(0, 47)}…` : value;
  }
}

function EmailText({ value }: { value: string }) {
  const parts = value.split(WEB_URL);
  const urls = value.match(WEB_URL) ?? [];
  return <>{parts.map((part, index) => <span key={`text-${index}`}>
    {part}
    {urls[index] && <a className="provider-email-link" href={urls[index]} target="_blank" rel="noreferrer" title={urls[index]}>{compactUrlLabel(urls[index])}</a>}
  </span>)}</>;
}

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
      ? <div className="provider-email-body-main"><EmailText value={sections.primary} /></div>
      : !hasSupportingContent && <p className="provider-email-body-empty">No message text was included.</p>}
    {sections.quoted.length > 0 && <details className="provider-email-disclosure">
      <summary>Show quoted or forwarded content ({sections.quoted.length})</summary>
      <div className="provider-email-disclosure-content">
        {sections.quoted.map((section, index) => (
          <div className="provider-email-body-main" key={`quoted-${index}`}><EmailText value={section} /></div>
        ))}
      </div>
    </details>}
    {sections.footers.length > 0 && <details className="provider-email-disclosure">
      <summary>Show security notices and email footer ({sections.footers.length})</summary>
      <div className="provider-email-disclosure-content">
        {sections.footers.map((section, index) => (
          <div className="provider-email-body-main" key={`footer-${index}`}><EmailText value={section} /></div>
        ))}
      </div>
    </details>}
  </div>;
}
