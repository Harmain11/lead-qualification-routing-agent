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
import "./corralAgentDock.css";

type Props = {
  selectedLead: Lead | null;
  canViewLeads: boolean;
  canEdit: boolean;
  canCrmSync: boolean;
  canViewEmail: boolean;
  onInspectLead: (leadId: string) => void;
  onUpdated: (lead: Lead) => void;
  onDeleted: (leadId: string) => void;
  onOpenLeadSection: (leadId: string, section: LeadDetailSection) => void;
  onRefresh: () => void | Promise<void>;
  permissions: ReadonlySet<string>;
  onNavigate: (area: WorkspaceAssistantArea) => void;
};

export function CorralAgentDock({
  selectedLead,
  canViewLeads,
  canEdit,
  canCrmSync,
  canViewEmail,
  onInspectLead,
  onUpdated,
  onDeleted,
  onOpenLeadSection,
  onRefresh,
  permissions,
  onNavigate,
}: Props) {
  const [open, setOpen] = useState(false);
  const [targetLead, setTargetLead] = useState<Lead | null>(selectedLead);
  const [showLeadPicker, setShowLeadPicker] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [panelVisible, setPanelVisible] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const closeTimerRef = useRef<number | null>(null);
  const openingFrameRef = useRef<number | null>(null);

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
      launcherRef.current?.focus({ preventScroll: true });
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
      .then((response) => setLeads(response.items))
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
  }, [open, showLeadPicker, canViewLeads, searchTerm]);

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
        aria-label="Corral Agent"
        aria-hidden={!open}
        inert={!open}
        hidden={!panelVisible}
      >
        <header className="corral-agent-window-header">
          <div className="corral-agent-window-brand">
            <span className="corral-agent-window-mark" aria-hidden="true">
              CA
            </span>
            <div>
              <strong>Corral Agent</strong>
              <small>
                {targetLead && !showLeadPicker
                  ? `${targetLead.full_name}${targetLead.company_name ? ` · ${targetLead.company_name}` : ""}`
                  : showLeadPicker
                    ? "Select a lead for actions"
                    : "Ask, generate, and work across Corral"}
              </small>
            </div>
          </div>
          <div className="corral-agent-window-controls">
            {targetLead && !showLeadPicker && (
              <button
                type="button"
                className="corral-agent-change-lead"
                onClick={() => {
                  setTargetLead(null);
                  setShowLeadPicker(false);
                }}
              >
                General chat
              </button>
            )}
            {canViewLeads && (
              <button
                type="button"
                className="corral-agent-change-lead"
                onClick={
                  showLeadPicker
                    ? () => setShowLeadPicker(false)
                    : changeLead
                }
              >
                {showLeadPicker
                  ? targetLead
                    ? "Back to lead"
                    : "Back to chat"
                  : targetLead
                    ? "Change lead"
                    : "Choose a lead"}
              </button>
            )}
            <button
              ref={closeButtonRef}
              type="button"
              className="corral-agent-close"
              aria-label="Collapse Corral Agent"
              onClick={closeAgent}
            >
              ×
            </button>
          </div>
        </header>

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
              <div className="corral-agent-start-icon" aria-hidden="true">
                CA
              </div>
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
                onClick={() => void onRefresh()}
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
        hidden={panelVisible}
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
