import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { listLeads } from "../api";
import type { WorkspaceAssistantArea } from "../api";
import type { Lead } from "../types";
import type { LeadDetailSection } from "../leadSections";
import { LeadAssistantPanel } from "./LeadAssistantPanel";
import { WorkspaceAssistantChat } from "./WorkspaceAssistantChat";
import { AGENT_STATE_EVENT, TOGGLE_AGENT_EVENT } from "./shell/agentEvents";
import { FeatureGuide } from "./FeatureGuide";

type Props = {
  selectedLead: Lead | null;
  canViewLeads: boolean;
  canEdit: boolean;
  canCrmSync: boolean;
  canViewEmail: boolean;
  dialerPhoneConnectionId: string | null;
  onInspectLead: (leadId: string) => void;
  onUpdated: (lead: Lead) => void;
  onDeleted: (leadId: string) => void;
  onOpenLeadSection: (leadId: string, section: LeadDetailSection) => void;
  onRefresh: () => void | Promise<void>;
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
  onNavigateToDialer: () => void;
};

export function CorralAgentDock({
  selectedLead,
  canViewLeads,
  canEdit,
  canCrmSync,
  canViewEmail,
  dialerPhoneConnectionId,
  onInspectLead,
  onUpdated,
  onDeleted,
  onOpenLeadSection,
  onRefresh,
  permissions,
  onNavigate,
  onNavigateToDialer,
}: Props) {
  const [open, setOpen] = useState(false);
  const [targetLead, setTargetLead] = useState<Lead | null>(selectedLead);
  const [showLeadPicker, setShowLeadPicker] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [leadRefresh, setLeadRefresh] = useState(0);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [panelVisible, setPanelVisible] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const closeTimerRef = useRef<number | null>(null);
  const openingFrameRef = useRef<number | null>(null);
  const openRef = useRef(false);

  const openAgent = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    if (openingFrameRef.current !== null) {
      window.cancelAnimationFrame(openingFrameRef.current);
    }
    setPanelVisible(true);
    openingFrameRef.current = window.requestAnimationFrame(() => {
      openingFrameRef.current = null;
      setOpen(true);
    });
  }, []);

  const closeAgent = useCallback(() => {
    if (openingFrameRef.current !== null) {
      window.cancelAnimationFrame(openingFrameRef.current);
      openingFrameRef.current = null;
    }
    setOpen(false);
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
    }
    closeTimerRef.current = window.setTimeout(() => {
      setPanelVisible(false);
      closeTimerRef.current = null;
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 320);
  }, []);

  // The pane is opened from the top bar ("Ask Corral") or Ctrl/Cmd+J, and it
  // announces its state so the layout can make room instead of covering content.
  useEffect(() => {
    const onToggle = (event: Event) => {
      const wanted = (event as CustomEvent<{ open?: boolean }>).detail?.open;
      const next = wanted ?? !openRef.current;
      if (next) openAgent();
      else closeAgent();
    };
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "j") {
        event.preventDefault();
        if (openRef.current) closeAgent();
        else openAgent();
      }
    };
    window.addEventListener(TOGGLE_AGENT_EVENT, onToggle);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(TOGGLE_AGENT_EVENT, onToggle);
      window.removeEventListener("keydown", onKey);
    };
  }, [openAgent, closeAgent]);

  useEffect(() => {
    openRef.current = open;
    window.dispatchEvent(new CustomEvent(AGENT_STATE_EVENT, { detail: { open } }));
  }, [open]);

  useEffect(
    () => () => {
      window.dispatchEvent(new CustomEvent(AGENT_STATE_EVENT, { detail: { open: false } }));
    },
    [],
  );

  useEffect(() => {
    if (selectedLead) {
      setTargetLead(selectedLead);
      setShowLeadPicker(false);
    }
  }, [selectedLead]);

  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      closeButtonRef.current?.focus({ preventScroll: true });
    }
  }, [open]);

  useEffect(() => {
    if (!panelVisible && wasOpen.current) {
      // Return focus to whichever control opened the pane (the toolbar button).
      const opener = document.querySelector<HTMLElement>("[data-ask-corral]:not([hidden])");
      (opener ?? launcherRef.current)?.focus({ preventScroll: true });
      wasOpen.current = false;
    }
  }, [panelVisible]);

  useEffect(
    () => () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
      }
      if (openingFrameRef.current !== null) {
        window.cancelAnimationFrame(openingFrameRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (!open || !showLeadPicker || !canViewLeads) return;
    const controller = new AbortController();
    setLoadingLeads(true);
    setLoadError("");
    listLeads(
      { limit: 8, offset: 0, search: searchTerm || undefined },
      controller.signal,
    )
      .then((response) => { if (!controller.signal.aborted) setLeads(response.items); })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setLoadError(
            error instanceof Error ? error.message : "Unable to load leads.",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingLeads(false);
      });
    return () => controller.abort();
  }, [open, showLeadPicker, canViewLeads, searchTerm, leadRefresh]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeAgent();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeAgent, open]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setSearchTerm(searchInput.trim());
  }

  function chooseLead(lead: Lead) {
    setTargetLead(lead);
    setShowLeadPicker(false);
    setSearchInput("");
    setSearchTerm("");
    onInspectLead(lead.id);
  }

  function changeLead() {
    setShowLeadPicker(true);
    setSearchInput("");
    setSearchTerm("");
    setLoadError("");
  }

  return (
    <div className="corral-agent-dock">
      <section
        id="corral-agent-window"
        className={[
          "corral-agent-window",
          panelVisible && "is-visible",
          open && "is-open",
        ]
          .filter(Boolean)
          .join(" ")}
        data-lead-active={Boolean(targetLead && !showLeadPicker)}
        aria-label="Corral Agent"
        aria-hidden={!open}
        inert={!open}
        hidden={!panelVisible}
      >
        <header className="v-agent-head">
          <div className="v-agent-title">
            <img src="/asset/brand/corral-mark.png" alt="" width="20" height="20" />
            <strong>Ask Corral</strong>
            <FeatureGuide topic="agent" />
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="v-iconbtn corral-agent-close"
            aria-label="Close Ask Corral"
            onClick={closeAgent}
          >
            <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </button>
        </header>
        <div className="v-agent-context" role="group" aria-label="What Corral is working on">
          <div className="v-agent-scope" role="group" aria-label="Conversation scope">
            <button
              type="button"
              aria-pressed={Boolean(targetLead) || showLeadPicker}
              disabled={!canViewLeads}
              onClick={() => {
                if (targetLead) setShowLeadPicker(false);
                else if (selectedLead) {
                  setTargetLead(selectedLead);
                  setShowLeadPicker(false);
                } else changeLead();
              }}
            >
              Lead
            </button>
            <button
              type="button"
              aria-pressed={!targetLead && !showLeadPicker}
              onClick={() => {
                setTargetLead(null);
                setShowLeadPicker(false);
              }}
            >
              Workspace
            </button>
          </div>
          <p className="v-agent-subject">
            {targetLead && !showLeadPicker ? (
              <>
                <b>{targetLead.company_name || targetLead.full_name}</b>
                <span>{targetLead.full_name}</span>
              </>
            ) : showLeadPicker ? (
              <span>Choose the lead Corral should work on</span>
            ) : (
              <span>Questions and tasks across this workspace</span>
            )}
          </p>
          {canViewLeads && (targetLead || showLeadPicker) && (
            <button
              type="button"
              className="v-btn corral-agent-change-lead"
              data-kind="plain"
              data-size="sm"
              onClick={showLeadPicker ? () => setShowLeadPicker(false) : changeLead}
            >
              {showLeadPicker ? (targetLead ? "Back to lead" : "Cancel") : "Change"}
            </button>
          )}
        </div>

        <div className="corral-agent-window-body">
          <div
            className="corral-agent-mode"
            hidden={Boolean(targetLead) || showLeadPicker}
          >
            <WorkspaceAssistantChat
              permissions={permissions}
              onNavigate={onNavigate}
              onInspectLead={onInspectLead}
              onRefresh={onRefresh}
              onLeadUpdated={onUpdated}
            />
          </div>
          {targetLead && (
            <div className="corral-agent-mode" hidden={showLeadPicker}>
            <LeadAssistantPanel
              lead={targetLead}
              canEdit={canEdit}
              canCrmSync={canCrmSync}
              canViewEmail={canViewEmail}
              canUseAgent
              dialerPhoneConnectionId={dialerPhoneConnectionId}
              onNavigateToDialer={onNavigateToDialer}
              onOpenLead={onInspectLead}
              onUpdated={(updated) => {
                setTargetLead(updated);
                onUpdated(updated);
              }}
              onDeleted={(leadId) => {
                setTargetLead(null);
                setShowLeadPicker(false);
                onDeleted(leadId);
              }}
              onSectionChange={(section) =>
                onOpenLeadSection(targetLead.id, section)
              }
            />
            </div>
          )}
          {showLeadPicker && (
            <div className="corral-agent-start">
              <img className="corral-agent-start-icon" src="/asset/brand/corral-mark.png" alt="" width="40" height="40" />
              <h2>What can I help with?</h2>
              <p>
                Choose a lead to get help with qualification, routing, notes,
                follow-ups, email, Gmail refresh, and CRM sync. You review
                proposed changes before they run.
              </p>
              {!canViewLeads ? (
                <p className="corral-agent-access-note" role="status">
                  Lead-view access is required to select a lead. Ask a workspace
                  admin to update your permissions.
                </p>
              ) : (
                <>
                  <form
                    className="corral-agent-search"
                    onSubmit={submitSearch}
                  >
                    <label htmlFor="corral-agent-lead-search">
                      Find a lead
                    </label>
                    <div>
                      <input
                        id="corral-agent-lead-search"
                        type="search"
                        value={searchInput}
                        onChange={(event) =>
                          setSearchInput(event.target.value)
                        }
                        placeholder="Name, company, or email"
                      />
                      <button className="primary-button" type="submit">
                        Search
                      </button>
                    </div>
                  </form>
                  {loadError && (
                    <p className="corral-agent-error" role="alert">
                      {loadError}
                    </p>
                  )}
                  {loadingLeads ? (
                    <p className="corral-agent-list-status" role="status">
                      Loading leads…
                    </p>
                  ) : (
                    <div className="corral-agent-lead-list">
                      <h3>{searchTerm ? "Search results" : "Recent leads"}</h3>
                      {leads.length ? (
                        leads.map((lead) => (
                          <button
                            type="button"
                            className="corral-agent-lead-option"
                            key={lead.id}
                            onClick={() => chooseLead(lead)}
                          >
                            <span>
                              <strong>{lead.full_name}</strong>
                              <small>
                                {[lead.company_name, lead.email]
                                  .filter(Boolean)
                                  .join(" · ") || "No company or email"}
                              </small>
                            </span>
                            <span aria-hidden="true">→</span>
                          </button>
                        ))
                      ) : (
                        <p className="corral-agent-list-status">
                          {searchTerm
                            ? "No matching leads found."
                            : "No leads are available yet."}
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
              <button
                type="button"
                className="corral-agent-refresh"
                onClick={() => { setLeadRefresh((value) => value + 1); void onRefresh(); }}
              >
                Refresh workspace data
              </button>
            </div>
          )}
        </div>
      </section>

      <button
        ref={launcherRef}
        type="button"
        className={`corral-agent-launcher${open ? " is-open" : ""}`}
        hidden
        aria-expanded={open}
        aria-controls="corral-agent-window"
        onClick={openAgent}
      >
        <span className="corral-agent-launcher-mark" aria-hidden="true">
          CA
        </span>
        <span>Corral Agent</span>
        <span className="corral-agent-launcher-state" aria-hidden="true">
          {open ? "−" : "+"}
        </span>
      </button>
    </div>
  );
}
