export const sampleGmailAccounts = [
  {
    id: "gmail-verified-sales",
    account_name: "sales@northstar.example",
    status: "verified",
    is_active: true,
    created_at: "2026-10-16T09:24:00Z",
  },
  {
    id: "gmail-verified-leads",
    account_name: "leads@northstar.example",
    status: "verified",
    is_active: true,
    created_at: "2026-10-15T14:10:00Z",
  },
  {
    id: "gmail-pending-1",
    account_name: "Pending gmail account",
    status: "awaiting_authorization",
    is_active: false,
    created_at: "2026-10-19T07:45:00Z",
  },
  {
    id: "gmail-pending-2",
    account_name: "Pending gmail account",
    status: "connecting",
    is_active: false,
    created_at: "2026-10-18T16:20:00Z",
  },
  {
    id: "gmail-pending-3",
    account_name: "Pending gmail account",
    status: "needs_reauthorization",
    is_active: false,
    created_at: "2026-10-17T11:05:00Z",
  },
  {
    id: "gmail-pending-4",
    account_name: "Pending gmail account",
    status: "authorization_expired",
    is_active: false,
    created_at: "2026-10-16T08:55:00Z",
  },
] as const;
