import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Older component styles live in one cascade layer; Corral v3 is unlayered and always wins.
import "./design/legacy.css";
import "./v3/foundation.css";
import "./v3/components.css";
import "./v3/shell.css";
import "./v3/screens.css";
import "./v3/compat.css";
import "./v3/auth.css";
import "./v3/v4.css";
import "./v3/polish.css";
import "./v3/audit.css";
import "./v3/mail.css";
import "./v3/calls.css";
import App from "./App";
import { ActivityToasts } from "./components/ActivityToasts";
import { ConfirmHost } from "./v3/ui/confirm";
import "./components/corralAgentMobile.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    <ActivityToasts />
    <ConfirmHost />
  </StrictMode>,
);
