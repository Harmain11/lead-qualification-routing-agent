import "./_group.css";
import "./compact.css";
import { ExpandedGmailCard } from "./_shared";

export function Compact() {
  return (
    <main className="app-shell integration-preview">
      <ExpandedGmailCard />
    </main>
  );
}
