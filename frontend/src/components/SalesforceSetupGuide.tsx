import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { IntegrationAccountChoice } from "./IntegrationAccountChoice";

import { SALESFORCE_PACKAGE_INSTALL_URL } from "../salesforceSetup";

type SalesforceSetupGuideProps = {
  onClose: () => void;
  onContinue: () => void;
  busy: boolean;
  canAuthorize?: boolean;
  error?: string;
  accountName?: string;
};

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function SalesforceSetupGuide({
  onClose,
  onContinue,
  busy,
  canAuthorize = true,
  error,
  accountName,
}: SalesforceSetupGuideProps) {
  const [packageInstalled, setPackageInstalled] = useState(false);
  const [accountReady, setAccountReady] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    return () => {
      document.body.style.overflow = previousOverflow;
      previousFocusRef.current?.focus();
    };
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!busy) onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
    if (!focusable?.length) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div
      className="salesforce-guide-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        className="salesforce-guide team-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="salesforce-guide-title"
        aria-describedby="salesforce-guide-intro"
        tabIndex={-1}
        ref={dialogRef}
        onKeyDown={handleKeyDown}
      >
        <header className="salesforce-guide-header">
          <div className="salesforce-guide-heading">
            <span className="eyebrow">Salesforce connection</span>
            <h2 id="salesforce-guide-title">Prepare your Salesforce org</h2>
            <p id="salesforce-guide-intro">
              Install Corral’s package in the org you intend to connect, then authorize access. Your Salesforce administrator can help with the install.
            </p>
          </div>
          <button
            ref={closeRef}
            className="salesforce-guide-close"
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close setup guide"
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <div className="salesforce-guide-content">
          <ol className="salesforce-guide-steps">
            <li className="salesforce-guide-step">
              <span className="salesforce-step-number" aria-hidden="true">01</span>
              <div className="salesforce-step-copy">
                <h3>Install the package in the right org</h3>
                <p>
                  Ask a Salesforce administrator with package-install permission to sign in to the Salesforce organization you want to connect. Before installing, review the package publisher, contents, and requested access.
                </p>
                <a
                  className="salesforce-package-link"
                  href={SALESFORCE_PACKAGE_INSTALL_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open Corral package installer <span aria-hidden="true">↗</span>
                </a>
                <p className="salesforce-guide-note">
                  The installer opens in a new tab. If your browser blocks it, use the link above to open it manually.
                </p>
              </div>
            </li>
            <li className="salesforce-guide-step">
              <span className="salesforce-step-number" aria-hidden="true">02</span>
              <div className="salesforce-step-copy">
                <h3>Wait for installation to finish</h3>
                <p>
                  Continue after Salesforce confirms success. If the package is already installed, verify it in <strong>Setup → Installed Packages</strong>; there is no need to reinstall it.
                </p>
              </div>
            </li>
            <li className="salesforce-guide-step">
              <span className="salesforce-step-number" aria-hidden="true">03</span>
              <div className="salesforce-step-copy">
                <h3>Return here to authorize Corral</h3>
                <p>
                  Confirm this is the same org, then continue to Salesforce to authorize Corral with the <code>api</code> and <code>refresh_token</code> scopes. Corral verifies provider access after authorization.
                </p>
              </div>
            </li>
          </ol>

          <label className="salesforce-installed-check">
            <input
              type="checkbox"
              checked={packageInstalled}
              onChange={(event) => setPackageInstalled(event.target.checked)}
              disabled={busy}
            />
            <span>I have installed this package in the Salesforce organization I want to connect</span>
          </label>

          <IntegrationAccountChoice provider="salesforce" accountName={accountName}
            busy={busy} onContinue={onContinue} onCancel={onClose}
            showActions={false} onReadyChange={setAccountReady} />

          {error && <p className="salesforce-guide-error" role="alert">{error}</p>}
          {!canAuthorize && <p className="salesforce-guide-note" role="status">Your workspace role can view this guide, but integration connection permission is required to authorize Salesforce.</p>}

          <details className="salesforce-troubleshooting">
            <summary>Administrator troubleshooting</summary>
            <ul>
              <li>Confirm the Salesforce user is active and can authorize connected apps.</li>
              <li>Verify the package is installed in the same org you are authorizing.</li>
              <li>Check that the configured callback URL, requested scopes, and PKCE settings align with the Corral OAuth configuration.</li>
            </ul>
             <p>Do not paste Salesforce passwords or OAuth app secrets into this guide. Sign in only on Salesforce’s website. Do not bypass your organization’s security policy or uninstall a package to troubleshoot.</p>
          </details>
        </div>

        <footer className="salesforce-guide-footer">
          <button className="button-quiet" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onContinue}
            disabled={busy || !packageInstalled || !accountReady || !canAuthorize}
          >
            {busy ? "Starting authorization…" : "Continue to Salesforce"}
          </button>
        </footer>
      </section>
    </div>,
    document.body
  );
}
