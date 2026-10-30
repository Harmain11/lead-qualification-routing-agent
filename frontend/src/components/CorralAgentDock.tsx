import {
  lazy,
  Suspense,
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

// The assistant is the heaviest part of the shell. It loads, and starts fetching
// chat history, only the first time someone opens it.
const WorkspaceAssistantChat = lazy(() =>
  import("./WorkspaceAssistantChat").then((module) => ({ default: module.WorkspaceAssistantChat })),
);
import { LeadAssistantPanel } from "./LeadAssistantPanel";
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
  const [everOpened, setEverOpened] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  const closeTimerRef = useRef<number | null>(null);
  const openingFrameRef = useRef<number | null>(null);
  const openRef = useRef(false);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const visualViewport = window.visualViewport;
    if (!panel || !visualViewport) return;

    const syncVisualViewport = () => {
      if (!window.matchMedia("(max-width: 767px)").matches) {
        panel.style.removeProperty("--corral-agent-visual-height");
        panel.style.removeProperty("--corral-agent-visual-top");
        panel.style.removeProperty("--corral-agent-visual-width");
        panel.style.removeProperty("--corral-agent-visual-left");
        return;
      }
      panel.style.setProperty(
        "--corral-agent-visual-height",
        `${Math.ceil(visualViewport.height)}px`,
      );
      panel.style.setProperty(
        "--corral-agent-visual-top",
        `${Math.max(0, Math.floor(visualViewport.offsetTop))}px`,
      );
      panel.style.setProperty(
        "--corral-agent-visual-width",
        `${Math.ceil(visualViewport.width)}px`,
      );
      panel.style.setProperty(
        "--corral-agent-visual-left",
        `${Math.max(0, Math.floor(visualViewport.offsetLeft))}px`,
      );
    };

    syncVisualViewport();
    visualViewport.addEventListener("resize", syncVisualViewport);
    visualViewport.addEventListener("scroll", syncVisualViewport);
    window.addEventListener("resize", syncVisualViewport);
    return () => {
      visualViewport.removeEventListener("resize", syncVisualViewport);
      visualViewport.removeEventListener("scroll", syncVisualViewport);
      window.removeEventListener("resize", syncVisualViewport);
      panel.style.removeProperty("--corral-agent-visual-height");
      panel.style.removeProperty("--corral-agent-visual-top");
      panel.style.removeProperty("--corral-agent-visual-width");
      panel.style.removeProperty("--corral-agent-visual-left");
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;

    const canPageScroll = (deltaY: number) => {
      if (document.querySelector("dialog[open], .lead-drawer-backdrop, .team-modal-backdrop")) {
        return false;
      }
      const root = document.scrollingElement;
      if (!root) return false;
      const maxScroll = Math.max(0, root.scrollHeight - root.clientHeight);
      return deltaY > 0 ? root.scrollTop < maxScroll - 1 : root.scrollTop > 1;
    };

    const innerCanScroll = (target: EventTarget | null, deltaY: number) => {
      let element = target instanceof Element ? target : null;
      while (element && element !== panel) {
        const overflowY = window.getComputedStyle(element).overflowY;
        const maxScroll = element.scrollHeight - element.clientHeight;
        if ((overflowY === "auto" || overflowY === "scroll") && maxScroll > 1) {
          if (deltaY > 0 && element.scrollTop < maxScroll - 1) return true;
          if (deltaY < 0 && element.scrollTop > 1) return true;
        }
        element = element.parentElement;
      }
      return false;
    };

    const forwardToPage = (
      target: EventTarget | null,
      deltaY: number,
      preventDefault: () => void,
      cancelable: boolean,
    ) => {
      if (
        !deltaY ||
        !cancelable ||
        innerCanScroll(target, deltaY) ||
        !canPageScroll(deltaY)
      ) {
        return;
      }
      preventDefault();
      window.scrollBy(0, deltaY);
    };

    const onWheel = (event: WheelEvent) => {
      if (event.defaultPrevented || event.ctrlKey) return;
      const multiplier = event.deltaMode === 1
        ? 16
        : event.deltaMode === 2
          ? window.innerHeight
          : 1;
      forwardToPage(
        event.target,
        event.deltaY * multiplier,
        () => event.preventDefault(),
        event.cancelable,
      );
    };

    let lastTouchY: number | null = null;
    let touchTarget: EventTarget | null = null;
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) {
        lastTouchY = null;
        touchTarget = null;
        return;
      }
      lastTouchY = event.touches[0].clientY;
      touchTarget = event.target;
    };
    const onTouchMove = (event: TouchEvent) => {
      if (event.touches.length !== 1 || lastTouchY === null) return;
      const nextY = event.touches[0].clientY;
      const deltaY = lastTouchY - nextY;
      lastTouchY = nextY;
      forwardToPage(
        touchTarget,
        deltaY,
        () => event.preventDefault(),
        event.cancelable,
      );
    };
    const clearTouch = () => {
      lastTouchY = null;
      touchTarget = null;
    };

    panel.addEventListener("wheel", onWheel, { capture: true, passive: false });
    panel.addEventListener("touchstart", onTouchStart, { capture: true, passive: true });
    panel.addEventListener("touchmove", onTouchMove, { capture: true, passive: false });
    panel.addEventListener("touchend", clearTouch, { capture: true, passive: true });
    panel.addEventListener("touchcancel", clearTouch, { capture: true, passive: true });
    return () => {
      panel.removeEventListener("wheel", onWheel, true);
      panel.removeEventListener("touchstart", onTouchStart, true);
      panel.removeEventListener("touchmove", onTouchMove, true);
      panel.removeEventListener("touchend", clearTouch, true);
      panel.removeEventListener("touchcancel", clearTouch, true);
    };
  }, [open]);

  const openAgent = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    if (openingFrameRef.current !== null) {
      window.cancelAnimationFrame(openingFrameRef.current);
    }
    setPanelVisible(true);
    setEverOpened(true);
    // One frame lets the panel mount before it slides in. Background tabs pause
    // animation frames, so a short timer guarantees it still opens.
    const reveal = () => {
      if (openingFrameRef.current !== null) window.cancelAnimationFrame(openingFrameRef.current);
      openingFrameRef.current = null;
      window.clearTimeout(openingFallback);
      setOpen(true);
    };
    const openingFallback = window.setTimeout(reveal, 60);
    openingFrameRef.current = window.requestAnimationFrame(reveal);
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
        ref={panelRef}
        id="corral-agent-window"
        className={[
          "corral-agent-window",
          panelVisible && "is-visible",
          open && "is-open",
        ]
          .filter(Boolean)
          .join(" ")}
        data-lead-active={Boolean(targetLead && !showLeadPicker)}
        aria-label="Ask Corral"
        aria-hidden={!open}
        inert={!open}
        hidden={!panelVisible}
      >
        <header className="v-agent-head">
          <div className="v-agent-title">
            <img src="/asset/brand/corral-mark.png" alt="" width="20" height="20" />
            <strong>Ask Corral</strong>
            <FeatureGuide topic="agent" compact />
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
          {everOpened && (
          <Suspense fallback={<p className="corral-agent-loading" role="status">Loading Corral…</p>}>
          <div
            className="corral-agent-mode"
            hidden={Boolean(targetLead) || showLeadPicker}
          >
            <WorkspaceAssistantChat
              permissions={permissions}
              onNavigate={onNavigate}
              onInspectLead={(leadId, section) =>
                section ? onOpenLeadSection(leadId, section) : onInspectLead(leadId)
              }
              onRefresh={onRefresh}
              onLeadUpdated={onUpdated}
            />
          </div>
          {targetLead && (
            <div className="corral-agent-mode" hidden={showLeadPicker}>
            <LeadAssistantPanel
              lead={targetLead}
              canEdit={canEdit}
              canManageManualReview={permissions.has("manual_review.manage")}
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
          </Suspense>
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
        <span>Ask Corral</span>
        <span className="corral-agent-launcher-state" aria-hidden="true">
          {open ? "−" : "+"}
        </span>
      </button>
    </div>
  );
}
