import { useEffect, useMemo, useState } from "react";
import { listLeads } from "../api";
import type { Lead } from "../types";
import { OPEN_DIALER_PHONE_EVENT } from "./dialerPhoneConfig";
import "./DialerContactDirectory.css";

type Props = {
  canCall: boolean;
  canCallLeads: boolean;
  embeddedPhoneSelected: boolean;
  phoneProviderName: string;
  preferredDialerId: string;
  dialerCallActive: boolean;
  onChooseLead: (lead: Lead) => void;
};

async function loadPhoneLeads(signal: AbortSignal) {
  const leads: Lead[] = [];
  let offset = 0;

  while (true) {
    const page = await listLeads({ limit: 100, offset }, signal);
    leads.push(...page.items.filter((lead) => Boolean(lead.phone?.trim())));
    if (!page.has_more || page.items.length === 0) break;
    offset += page.items.length;
  }

  return leads.sort((a, b) =>
    a.full_name.localeCompare(b.full_name, undefined, { sensitivity: "base" }),
  );
}

export function DialerContactDirectory({
  canCall,
  canCallLeads,
  embeddedPhoneSelected,
  phoneProviderName,
  preferredDialerId,
  dialerCallActive,
  onChooseLead,
}: Props) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void loadPhoneLeads(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setLeads(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load leads and contacts with phone numbers.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refreshKey]);

  const visibleLeads = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return leads;
    return leads.filter((lead) =>
      [lead.full_name, lead.company_name, lead.email, lead.phone]
        .some((value) => value?.toLocaleLowerCase().includes(query)),
    );
  }, [leads, search]);

  async function copyNumber(lead: Lead) {
    const phone = lead.phone?.trim();
    if (!phone) return;
    try {
      await navigator.clipboard.writeText(phone);
      setFeedback(`${phone} copied to clipboard.`);
    } catch {
      setFeedback(`Clipboard access is unavailable. Select and copy ${phone} manually.`);
    }
  }

  async function openPhoneForLead(lead: Lead) {
    if (embeddedPhoneSelected && preferredDialerId) {
      const phone = lead.phone?.trim();
      let copied = false;
      if (phone) {
        try {
          await navigator.clipboard.writeText(phone);
          copied = true;
        } catch {
          // The phone still opens; the number remains visible in the directory.
        }
      }
      window.dispatchEvent(
        new CustomEvent(OPEN_DIALER_PHONE_EVENT, {
          detail: { connectionId: preferredDialerId },
        }),
      );
      setFeedback(
        copied
          ? `${phone} copied. Paste it into ${phoneProviderName} Phone and place the call there.`
          : `Opened ${phoneProviderName} Phone. Copy the number from the directory and dial it there.`,
      );
      return;
    }

    onChooseLead(lead);
  }

  const callDisabled =
    !canCall ||
    dialerCallActive ||
    (!embeddedPhoneSelected && !canCallLeads);

  return (
    <section className="dialer-contact-directory" aria-label="Leads and contacts with phone numbers">
      <div className="dialer-contact-directory-head">
        <div>
          <span className="eyebrow">Phone directory</span>
          <h3>Leads &amp; contacts</h3>
          <p>
            {loading
              ? "Loading saved phone numbers…"
              : `${leads.length} lead${leads.length === 1 ? "" : "s"} with a phone number`}
          </p>
        </div>
        <button
          type="button"
          className="button-quiet"
          onClick={() => setRefreshKey((value) => value + 1)}
          disabled={loading}
        >
          Refresh
        </button>
      </div>

      <label className="dialer-contact-search">
        Search by name, company, email, or phone
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search phone directory…"
        />
      </label>

      {error && (
        <div className="dialer-contact-error" role="alert">
          <span>{error}</span>
          <button type="button" className="button-quiet" onClick={() => setRefreshKey((value) => value + 1)}>
            Retry
          </button>
        </div>
      )}
      {feedback && <p className="dialer-contact-feedback" role="status">{feedback}</p>}
      {embeddedPhoneSelected && (
        <p className="dialer-contact-call-note">
          Call opens {phoneProviderName} Phone and copies the number; paste it into the phone to
          place the call. Corral does not start calls from the embedded phone.
          {phoneProviderName === "CloudTalk" &&
            " CloudTalk keypad calls bypass Corral’s consent, do-not-call, and residency checks."}
        </p>
      )}

      {loading ? (
        <p className="dialer-empty" role="status">Loading phone directory…</p>
      ) : visibleLeads.length ? (
        <div className="dialer-contact-list" role="list" aria-label="Leads with phone numbers">
          {visibleLeads.map((lead) => (
            <article className="dialer-contact-row" role="listitem" key={lead.id}>
              <button
                type="button"
                className="dialer-contact-main"
                onClick={() => onChooseLead(lead)}
                aria-label={`Open call options for ${lead.full_name}`}
              >
                <strong>{lead.full_name || "Unnamed lead"}</strong>
                <span>
                  {[lead.company_name, lead.email].filter(Boolean).join(" · ") || "Contact"}
                </span>
                <bdi>{lead.phone}</bdi>
              </button>
              <div className="dialer-contact-actions">
                <button
                  type="button"
                  className="dialer-contact-action"
                  onClick={() => void openPhoneForLead(lead)}
                  disabled={callDisabled}
                  aria-label={`Call ${lead.full_name}`}
                  title={
                    !canCall
                      ? "Dialer calling permission is required."
                      : dialerCallActive
                        ? "Finish the active call before starting another."
                        : embeddedPhoneSelected
                          ? `Open ${phoneProviderName} Phone with this number copied.`
                          : !canCallLeads
                            ? "Calling this lead requires lead-edit access."
                            : `Open call confirmation for ${lead.full_name}.`
                  }
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M7.2 3.5 10 7.8 8.4 9.5c1 2.1 2.6 3.7 4.7 4.7l1.7-1.6 4.3 2.8-.8 3.2c-.2.7-.9 1.2-1.7 1.2C9 19.8 4.2 15 4.2 7.4c0-.8.5-1.5 1.2-1.7l1.8-.5Z" />
                  </svg>
                </button>
                <button
                  type="button"
                  className="dialer-contact-action"
                  onClick={() => void copyNumber(lead)}
                  aria-label={`Copy phone number for ${lead.full_name}`}
                  title={`Copy ${lead.phone}`}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M8 8V4.8c0-.4.3-.8.8-.8h10.4c.4 0 .8.3.8.8v10.4c0 .4-.3.8-.8.8H16M5 8h10.2c.4 0 .8.3.8.8V19c0 .4-.3.8-.8.8H5c-.4 0-.8-.3-.8-.8V8.8c0-.4.3-.8.8-.8Z" />
                  </svg>
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="dialer-empty">
          {search ? "No phone numbers match that search." : "No leads with phone numbers are available."}
        </p>
      )}
    </section>
  );
}
