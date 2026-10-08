import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import App from "./App";
import "./components/navigationMenus.css";
import "./components/usabilityFixes.css";
import "./components/navigationLayout.css";
import "./components/leadDetailPolish.css";
import "./components/subpages.css";
import "./components/layoutConsistency.css";
import "./components/leadBrowserLayout.css";
import "./components/tableTypography.css";
import "./components/brandButtons.css";
import "./components/integrationExpansion.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
