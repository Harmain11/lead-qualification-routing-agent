import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./ProductTour.css";


export type TourView =
  | "dashboard"
  | "leads"
  | "email"
  | "calendar"
  | "communication"
  | "contacts"
  | "pipeline"
  | "revenue"
  | "performance"
  | "agents"
  | "review"
  | "integrations"
  | "dialer"
  | "analytics"
  | "settings"
  | "team"
  | "icp" | "accounts" | "intelligence" | "automations" | "policies" | "compliance";

type ProductTourProps = {
  open: boolean;
  currentView: TourView;
  onNavigate: (view: TourView) => void;
  onDismiss: () => void;
  onComplete: () => void;
};

type TourStep = {
  view: TourView;
  title: string;
  description: string;
  note: string;
};

const steps: TourStep[] = [
  {
    view: "dashboard",
    title: "Start with the full picture",
    description: "The Dashboard brings lead volume, qualification, pipeline value, and live operational activity into one workspace overview.",
    note: "Use it to spot changes and decide where the team should look next.",
  },
  {
    view: "leads",
    title: "Keep every lead traceable",
    description: "Leads is the working record for inbound signals. Search, filter, inspect qualification evidence, and see routing and CRM delivery in context.",
    note: "A lead’s journey stays connected to its original source and decisions.",
  },
  {
    view: "pipeline",
    title: "See how work moves",
    description: "Pipeline groups leads by stage so the team can understand where opportunities are progressing and where attention may be needed.",
    note: "Select a stage to inspect the records behind its totals.",
  },
  {
    view: "agents",
    title: "Inspect the AI’s work",
    description: "Agents / AI Activity shows what automated capabilities are available and the recent, auditable events they produce.",
    note: "Corral makes operational outcomes visible without exposing hidden model reasoning.",
  },
  {
    view: "review",
    title: "Keep people in the loop",
    description: "Manual Review gathers leads that need a person to make or confirm a decision before work continues.",
    note: "Human review is part of the workflow, not an exception to it.",
  },
  {
    view: "integrations",
    title: "Connect trusted systems",
    description: "Integrations is where workspace admins connect inbound sources, CRM systems, AI services, and notification destinations.",
    note: "Connections and outbound actions remain explicit and permission-aware.",
  },
  {
    view: "analytics",
    title: "Understand what is working",
    description: "Analytics summarizes lead quality, channel performance, pipeline movement, and commercial outcomes over the selected period.",
    note: "Revenue figures include their source so the team can judge them in context.",
  },
  {
    view: "settings",
    title: "Set the rules for your workspace",
    description: "Settings is the home for workspace configuration. Configure your Ideal Customer Profile (ICP) and manage the team and permissions from here.",
    note: "These choices shape how leads are evaluated and who can take action.",
  },
  {
    view: "team",
    title: "Give the right people access",
    description: "Team & Permissions lets workspace admins invite members, assign roles, and review the permissions available to each person.",
    note: "Access is scoped to the current workspace. If a control is unavailable, ask an owner or admin.",
  },
  {
    view: "icp",
    title: "Define and test customer fit",
    description: "The ICP Builder lets authorized users define qualification criteria, validate a draft, and test its effect before publishing.",
    note: "Published ICP versions are immutable so past qualification decisions stay traceable.",
  },
];

type Spotlight = { x: number; y: number; width: number; height: number };

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

export function ProductTour({
  open,
  currentView,
  onNavigate,
  onDismiss,
  onComplete,
}: ProductTourProps) {
  const [mode, setMode] = useState<"welcome" | "steps">("welcome");
  const [stepIndex, setStepIndex] = useState(0);
  const [spotlight, setSpotlight] = useState<Spotlight | null>(null);
  const [cardPosition, setCardPosition] = useState<{ top: number; left: number } | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const initialFocusRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const dismissRef = useRef(onDismiss);
  const cardSizeRef = useRef({ width: 410, height: 430 });
  const titleId = useId();
  const descriptionId = useId();
  const maskId = `corral-tour-mask-${useId().replaceAll(":", "")}`;
  const activeStep = steps[stepIndex];
  const activeSectionTitle = activeStep.view === "agents"
    ? "Agents / AI Activity"
    : activeStep.view === "review"
      ? "Manual Review"
      : activeStep.view === "team"
        ? "Team & Permissions"
        : activeStep.view === "icp"
          ? "Ideal Customer Profile"
          : activeStep.view[0].toUpperCase() + activeStep.view.slice(1);
  const progressLabel = useMemo(
    () => `Step ${stepIndex + 1} of ${steps.length}`,
    [stepIndex],
  );

  dismissRef.current = onDismiss;

  useEffect(() => {
    if (!open) return;
    setMode("welcome");
    setStepIndex(0);
    setSpotlight(null);
    setCardPosition(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      (initialFocusRef.current ?? closeRef.current)?.focus();
    });
    return () => {
      window.cancelAnimationFrame(frame);
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismissRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.getAttribute("aria-hidden") !== "true");
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !dialogRef.current) return;
    const dialog = dialogRef.current;
    const measureCard = () => {
      const rect = dialog.getBoundingClientRect();
      cardSizeRef.current = { width: rect.width, height: rect.height };
      if (!spotlight) {
        setCardPosition({
          left: Math.max(16, (window.innerWidth - rect.width) / 2),
          top: Math.max(16, (window.innerHeight - rect.height) / 2),
        });
      } else {
        const margin = 18;
        const gap = 18;
        const below = spotlight.y + spotlight.height + gap;
        const above = spotlight.y - rect.height - gap;
        let left = spotlight.x + spotlight.width / 2 - rect.width / 2;
        let top = below;
        if (below + rect.height > window.innerHeight - margin && above >= margin) {
          top = above;
        } else if (below + rect.height > window.innerHeight - margin) {
          const right = spotlight.x + spotlight.width + gap;
          const leftSide = spotlight.x - rect.width - gap;
          if (right + rect.width <= window.innerWidth - margin) {
            left = right;
            top = clamp(spotlight.y + spotlight.height / 2 - rect.height / 2, margin, window.innerHeight - rect.height - margin);
          } else if (leftSide >= margin) {
            left = leftSide;
            top = clamp(spotlight.y + spotlight.height / 2 - rect.height / 2, margin, window.innerHeight - rect.height - margin);
          } else {
            top = Math.max(margin, (window.innerHeight - rect.height) / 2);
          }
        }
        setCardPosition({
          left: clamp(left, margin, Math.max(margin, window.innerWidth - rect.width - margin)),
          top: clamp(top, margin, Math.max(margin, window.innerHeight - rect.height - margin)),
        });
      }
    };
    measureCard();
    const observer = new ResizeObserver(measureCard);
    observer.observe(dialog);
    window.addEventListener("resize", measureCard);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measureCard);
    };
  }, [open, mode, spotlight, stepIndex]);

  useEffect(() => {
    if (!open || mode !== "steps" || !activeStep) {
      setSpotlight(null);
      return;
    }
    let frameOne = 0;
    let frameTwo = 0;
    let target: HTMLElement | null = null;
    let targetResizeObserver: ResizeObserver | null = null;
    const measureTarget = () => {
      if (!target || !target.isConnected || currentView !== activeStep.view) {
        setSpotlight(null);
        return;
      }
      const rect = target.getBoundingClientRect();
      setSpotlight({
        x: Math.max(8, rect.left - 8),
        y: Math.max(8, rect.top - 8),
        width: Math.min(window.innerWidth - 16, rect.width + 16),
        height: Math.min(window.innerHeight - 16, rect.height + 16),
      });
    };
    const findTarget = () => {
      const nextTarget = Array.from(document.querySelectorAll<HTMLElement>("[data-tour]"))
        .find((element) => element.dataset.tour === activeStep.view) ?? null;
      if (nextTarget !== target) {
        targetResizeObserver?.disconnect();
        target = nextTarget;
        if (target) {
          targetResizeObserver = new ResizeObserver(measureTarget);
          targetResizeObserver.observe(target);
          const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center", inline: "nearest" });
        }
      }
      measureTarget();
    };
    frameOne = window.requestAnimationFrame(() => {
      frameTwo = window.requestAnimationFrame(() => {
        findTarget();
      });
    });
    window.addEventListener("scroll", measureTarget, true);
    window.addEventListener("resize", measureTarget);
    const mutationObserver = new MutationObserver(findTarget);
    mutationObserver.observe(document.body, { childList: true, subtree: true });
    return () => {
      window.cancelAnimationFrame(frameOne);
      window.cancelAnimationFrame(frameTwo);
      window.removeEventListener("scroll", measureTarget, true);
      window.removeEventListener("resize", measureTarget);
      mutationObserver.disconnect();
      targetResizeObserver?.disconnect();
    };
  }, [open, mode, activeStep, currentView]);

  useEffect(() => {
    if (!open || mode !== "steps") return;
    const frame = window.requestAnimationFrame(() => {
      const primary = dialogRef.current?.querySelector<HTMLElement>("[data-tour-primary]");
      primary?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, mode, stepIndex]);

  if (!open || typeof document === "undefined") return null;

  const startTour = () => {
    setStepIndex(0);
    setMode("steps");
    onNavigate(steps[0].view);
  };

  const goToStep = (nextIndex: number) => {
    const bounded = clamp(nextIndex, 0, steps.length - 1);
    setStepIndex(bounded);
    setSpotlight(null);
    onNavigate(steps[bounded].view);
  };

  const spotlightRect = spotlight
    ? { x: spotlight.x, y: spotlight.y, width: spotlight.width, height: spotlight.height }
    : null;

  return createPortal(
    <div className="ct-tour-root">
      <div className="ct-tour-backdrop">
        {spotlightRect ? (
          <>
            <svg className="ct-tour-dimmer" aria-hidden="true">
              <defs>
                <mask id={maskId}>
                  <rect width="100%" height="100%" fill="white" />
                  <rect
                    x={spotlightRect.x}
                    y={spotlightRect.y}
                    width={spotlightRect.width}
                    height={spotlightRect.height}
                    rx="14"
                    fill="black"
                  />
                </mask>
              </defs>
              <rect width="100%" height="100%" fill="rgba(12,25,21,.58)" mask={`url(#${maskId})`} />
            </svg>
            <div
              className="ct-tour-spotlight"
              aria-hidden="true"
              style={{
                position: "fixed",
                zIndex: 1,
                left: spotlightRect.x,
                top: spotlightRect.y,
                width: spotlightRect.width,
                height: spotlightRect.height,
                border: "1px solid rgba(242,165,65,.9)",
                borderRadius: 14,
                boxShadow: "0 0 0 4px rgba(242,165,65,.16)",
                pointerEvents: "none",
              }}
            />
          </>
        ) : (
          <div className="ct-tour-dimmer" style={{ background: "rgba(12,25,21,.58)" }} aria-hidden="true" />
        )}
        <section
          ref={dialogRef}
          className="ct-tour-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={descriptionId}
          tabIndex={-1}
          style={cardPosition ? { top: cardPosition.top, left: cardPosition.left } : undefined}
        >
          <div className="ct-tour-topline">
            <div className="ct-tour-brand"><span className="ct-tour-mark" aria-hidden="true" />Corral <span aria-hidden="true">/</span> Workspace tour</div>
            <button
              ref={closeRef}
              className="ct-tour-close"
              type="button"
              onClick={onDismiss}
              aria-label="Close product tour"
              title="Close tour"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
            </button>
          </div>

          {mode === "welcome" ? (
            <>
              <span className="ct-tour-kicker">Product guide</span>
              <h2 id={titleId} className="ct-tour-title">A quick tour when you need it.</h2>
              <p id={descriptionId} className="ct-tour-description">
                See where leads arrive, how they are qualified, and where you can take action. This guide only opens from the Guide menu.
              </p>
              <div className="ct-tour-flow" aria-label="Corral lead workflow: signal, decision, delivery">
                <span><i>01</i>Signal</span>
                <span><i>02</i>Decision</span>
                <span><i>03</i>Delivery</span>
              </div>
              <div className="ct-tour-actions">
                <button ref={initialFocusRef} className="ct-tour-button ct-tour-button-quiet" type="button" onClick={onDismiss}>
                  Close
                </button>
                <button className="ct-tour-button ct-tour-button-primary" type="button" onClick={startTour} data-tour-primary>
                  Start tour <span aria-hidden="true">›</span>
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="ct-tour-progress" aria-label={progressLabel}>
                <span>Corral, section by section</span>
                <strong>{String(stepIndex + 1).padStart(2, "0")} / {String(steps.length).padStart(2, "0")}</strong>
              </div>
              <div
                className="ct-tour-progress-track"
                role="progressbar"
                aria-label="Tour progress"
                aria-valuemin={1}
                aria-valuemax={steps.length}
                aria-valuenow={stepIndex + 1}
              >
                {steps.map((step, index) => (
                  <i
                    key={step.view}
                    className={index < stepIndex ? "complete" : index === stepIndex ? "current" : ""}
                    aria-hidden="true"
                  />
                ))}
              </div>
              <div className="ct-tour-step-body" aria-live="polite" aria-atomic="true">
                <span className="ct-tour-kicker ct-tour-step-label"><i aria-hidden="true" />{activeStep.title}</span>
                <h2 id={titleId} className="ct-tour-title ct-tour-step-title">{activeSectionTitle}</h2>
                <p id={descriptionId} className="ct-tour-description">{activeStep.description}</p>
                <div className="ct-tour-note"><b>Good to know</b><span>{activeStep.note}</span></div>
              </div>
              <div className="ct-tour-actions">
                <button
                  className="ct-tour-button ct-tour-button-quiet"
                  type="button"
                  onClick={onDismiss}
                >
                  Skip tour
                </button>
                <div className="ct-tour-actions-side">
                  <button
                    className="ct-tour-button ct-tour-button-secondary"
                    type="button"
                    onClick={() => goToStep(stepIndex - 1)}
                    disabled={stepIndex === 0}
                    aria-label="Go to previous section"
                  >
                    Back
                  </button>
                  {stepIndex < steps.length - 1 ? (
                    <button
                      className="ct-tour-button ct-tour-button-primary"
                      type="button"
                      onClick={() => goToStep(stepIndex + 1)}
                      data-tour-primary
                    >
                      Next <span aria-hidden="true">›</span>
                    </button>
                  ) : (
                    <button
                      className="ct-tour-button ct-tour-button-primary"
                      type="button"
                      onClick={onComplete}
                      data-tour-primary
                    >
                      Finish <span aria-hidden="true">✓</span>
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>,
    document.body,
  );
}
