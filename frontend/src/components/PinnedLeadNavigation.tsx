import { useEffect, useMemo, useState } from "react";
import { leadDetailSections, leadSectionHref, type LeadDetailSection } from "../leadSections";

export type PinnedLeadSection = { leadId: string; company: string; section: LeadDetailSection };
const samePin = (a: PinnedLeadSection, b: PinnedLeadSection) => a.leadId === b.leadId && a.section === b.section;

function readPins(key: string | null): PinnedLeadSection[] {
  if (!key) return [];
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(parsed)) return [];
    const pins: PinnedLeadSection[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== "object" || typeof item.leadId !== "string" ||
          !/^[0-9a-f-]{36}$/i.test(item.leadId) || typeof item.company !== "string" ||
          !leadDetailSections.some((section) => section.id === item.section)) continue;
      const pin = { leadId: item.leadId, company: item.company.slice(0, 200), section: item.section as LeadDetailSection };
      if (!pins.some((existing) => samePin(existing, pin))) pins.push(pin);
      if (pins.length === 30) break;
    }
    return pins;
  } catch { return []; }
}

export function usePinnedLeadSections(userId?: string, workspaceId?: string) {
  const key = userId && workspaceId ? `corral.lead.pins.v1:${userId}:${workspaceId}` : null;
  const stored = useMemo(() => readPins(key), [key]);
  const [state, setState] = useState<{ key: string | null; pins: PinnedLeadSection[]; notice: string | null }>({ key, pins: stored, notice: null });
  const pins = state.key === key ? state.pins : stored;
  const notice = state.key === key ? state.notice : null;
  useEffect(() => {
    setState({ key, pins: stored, notice: null });
  }, [key, stored]);
  useEffect(() => {
    const update = (event: StorageEvent) => {
      if (event.key === key || event.key === null) setState({ key, pins: readPins(key), notice: null });
    };
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, [key]);

  const save = (next: PinnedLeadSection[]) => {
    if (!key) return;
    let nextNotice = null;
    try { localStorage.setItem(key, JSON.stringify(next)); }
    catch { nextNotice = "Pins are available for this session only; browser storage is unavailable."; }
    setState({ key, pins: next, notice: nextNotice });
  };
  const remove = (pin: PinnedLeadSection) => save(pins.filter((item) => !samePin(item, pin)));
  const toggle = (pin: PinnedLeadSection) => {
    if (pins.some((item) => samePin(item, pin))) remove(pin);
    else if (pins.length < 30) save([...pins, pin]);
    else setState({ key, pins, notice: "You can pin up to 30 sections. Unpin a section to add another." });
  };
  return { pins, notice, toggle, remove, removeLead: (id: string) => save(pins.filter((pin) => pin.leadId !== id)) };
}

export function PinnedLeadNavigation({ pins, notice, activeLeadId, activeSection, onOpen, onRemove }: {
  pins: PinnedLeadSection[];
  notice: string | null;
  activeLeadId?: string;
  activeSection: LeadDetailSection;
  onOpen: (pin: PinnedLeadSection) => void;
  onRemove: (pin: PinnedLeadSection) => void;
}) {
  if (!pins.length && !notice) return null;
  return <section className="pinned-lead-navigation" aria-label="Pinned lead sections">
    <h3>Pinned sections <span>{pins.length}</span></h3>
    <div className="pinned-lead-list">{pins.map((pin) => {
      const label = leadDetailSections.find((section) => section.id === pin.section)!.label;
      const active = activeLeadId === pin.leadId && activeSection === pin.section;
      return <div className="pinned-lead-row" key={`${pin.leadId}:${pin.section}`}>
        <a href={leadSectionHref(pin.leadId, pin.section)} aria-current={active ? "page" : undefined}
          onClick={(event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onOpen(pin); } }}>
          <span>{pin.company}</span><small>{label}</small>
        </a>
        <button type="button" onClick={() => onRemove(pin)} aria-label={`Unpin ${[pin.company, label].filter(Boolean).join(" · ")}`} title="Unpin section">×</button>
      </div>;
    })}</div>
    {notice && <p role="status">{notice}</p>}
  </section>;
}
