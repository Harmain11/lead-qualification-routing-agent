import { reloadDealRecords, retryDealChanges, useDeals } from "./dealStore";
import { confirmAction } from "../ui/confirm";

export function DealSyncStatus() {
  const store = useDeals();
  if (store.syncStatus !== "error") return null;
  if (!store.dirty) return <div className="v-banner" data-tone="danger" role="alert">
    <div><strong>Deal records could not load</strong><p>{store.syncError} Try again when the workspace is reachable.</p></div>
    <button type="button" className="v-btn" onClick={() => void retryDealChanges()}>Retry loading</button>
  </div>;
  return <div className="v-banner" data-tone="danger" role="alert">
    <div><strong>Deal changes need attention</strong><p>{store.syncError} Your unsaved draft is kept in this browser.</p></div>
    <button type="button" className="v-btn" onClick={() => void retryDealChanges()}>Retry save</button>
    <button type="button" className="v-btn" onClick={() => {
      void confirmAction({ title: "Reload saved records?", message: "This replaces your current unsaved deal draft.", confirmLabel: "Reload", tone: "danger" })
        .then((confirmed) => { if (confirmed) void reloadDealRecords(); });
    }}>Reload saved records</button>
  </div>;
}
