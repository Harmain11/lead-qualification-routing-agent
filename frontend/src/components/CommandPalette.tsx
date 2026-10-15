import { useEffect, useRef, useState } from "react";
import { listLeads } from "../api";
import { opsGet, type Company, type Contact } from "../operationsApi";
import { OperationsDialog } from "./OperationsDialog";
import type { AppPage } from "../appNavigation";
type Result = {
  id: string;
  label: string;
  kind: "Lead" | "Contact" | "Company";
};
export function CommandPalette({
  pages,
  canSearch,
  onNavigate,
  onInspect,
}: {
  pages: { id: AppPage; label: string }[];
  canSearch: boolean;
  onNavigate: (id: AppPage) => void;
  onInspect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const resultList = useRef<HTMLDivElement>(null);
  const matchedPages = pages.filter((page) => page.label.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    setResults([]);
    setError("");
    if (!open || query.trim().length < 2 || !canSearch) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(
      () =>
        void Promise.all([
          listLeads({ search: query, limit: 8 }, controller.signal).then(
            (data) =>
              data.items.map((item) => ({
                id: item.id,
                label: `${item.full_name} · ${item.company_name}`,
                kind: "Lead" as const,
              })),
          ),
          opsGet<{ items: Contact[] }>(
            `/v1/records/contacts?search=${encodeURIComponent(query)}&limit=5`,
            controller.signal,
          ).then((data) =>
            data.items.map((item) => ({
              id: item.id,
              label: item.name + " · " + (item.email || ""),
              kind: "Contact" as const,
            })),
          ),
          opsGet<{ items: Company[] }>(
            `/v1/records/companies?search=${encodeURIComponent(query)}&limit=5`,
            controller.signal,
          ).then((data) =>
            data.items.map((item) => ({
              id: item.id,
              label: item.name + " · " + (item.domain || ""),
              kind: "Company" as const,
            })),
          ),
        ])
          .then((groups) => {
            if (!controller.signal.aborted) setResults(groups.flat());
          })
          .catch((reason) => {
            if (!controller.signal.aborted) setError(reason.message);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          }),
      250,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, open, canSearch]);
  return (
    <>
      <button
        className="secondary-button command-launch"
        onClick={() => {
          setQuery("");
          setOpen(true);
        }}
        aria-haspopup="dialog"
      >
        Search
      </button>
      {open && (
        <OperationsDialog
          title="Search Corral"
          className="command-palette"
          onClose={() => setOpen(false)}
        >
          <div className="command-palette-body" onKeyDown={(event) => {
            const buttons = [...(resultList.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
            if (!buttons.length) return;
            const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const next = current < 0 ? (event.key === "ArrowDown" ? 0 : buttons.length - 1) : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
              buttons[next].focus();
            } else if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
              event.preventDefault();
              buttons[0].click();
            }
          }}>
          <label className="command-search-field">
            Search pages, leads, contacts and companies
            <input
              autoFocus
              type="search"
              placeholder="Search Corral…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <p className="ops-muted">
            Use ↑ ↓ to move, Enter to open, or Escape to close.
          </p>
          <div className="ops-result-list" ref={resultList} aria-label="Search results">
            {matchedPages.map((page) => (
                <button
                  className="secondary-button"
                  key={page.id}
                  onClick={() => {
                    onNavigate(page.id);
                    setOpen(false);
                  }}
                >
                  <span className="command-kind">Page</span>
                  <span className="command-result-label">{page.label}</span>
                  <span aria-hidden="true" className="command-result-arrow">↵</span>
                </button>
              ))}
            {results.map((result) => (
              <button
                className="secondary-button"
                key={result.kind + result.id}
                onClick={() => {
                  if (result.kind === "Lead") onInspect(result.id);
                  else {
                    window.sessionStorage.setItem(
                      "corral-record-target",
                      JSON.stringify({ kind: result.kind, id: result.id }),
                    );
                    onNavigate(
                      result.kind === "Company" ? "accounts" : "contacts",
                    );
                    window.dispatchEvent(new Event("corral-record-target"));
                  }
                  setOpen(false);
                }}
              >
                <span className="command-kind">{result.kind}</span>
                <span className="command-result-label">{result.label}</span>
                <span aria-hidden="true" className="command-result-arrow">↵</span>
              </button>
            ))}
          </div>
          {loading && <p role="status">Searching…</p>}
          {error && <p role="alert">{error}</p>}
          {!loading && !error && !matchedPages.length && !results.length && <p className="command-empty" role="status">{query.trim().length < 2 ? "Enter at least two characters to search records." : "No matching pages or records. Try a name, company or email."}</p>}
          </div>
        </OperationsDialog>
      )}
    </>
  );
}
