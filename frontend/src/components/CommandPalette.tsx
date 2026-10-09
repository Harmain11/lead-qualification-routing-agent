import { useEffect, useState } from "react";
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
        Search <kbd>Ctrl K</kbd>
      </button>
      {open && (
        <OperationsDialog
          title="Commands & records"
          onClose={() => setOpen(false)}
        >
          <label>
            Search pages, leads, contacts and companies
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <p className="ops-muted">
            Use Tab and Enter to select a result. Escape closes this window.
          </p>
          <div className="ops-result-list">
            {pages
              .filter((page) =>
                page.label.toLowerCase().includes(query.toLowerCase()),
              )
              .map((page) => (
                <button
                  className="secondary-button"
                  key={page.id}
                  onClick={() => {
                    onNavigate(page.id);
                    setOpen(false);
                  }}
                >
                  <span>Page</span>
                  {page.label}
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
                <span>{result.kind}</span>
                {result.label}
              </button>
            ))}
          </div>
          {loading && <p role="status">Searching…</p>}
          {error && <p role="alert">{error}</p>}
        </OperationsDialog>
      )}
    </>
  );
}
