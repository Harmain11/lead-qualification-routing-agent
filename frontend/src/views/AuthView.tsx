import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { getPasswordSignupStatus, resendVerification, signIn, signInWithWorkspaceCode, signUp, startDevelopmentSession, startGoogleAuth } from "../api";
import type { AuthSession } from "../types";

const BRAND = "/asset/brand";

export function AuthView({ onAuthenticated, theme, onToggleTheme }: { onAuthenticated: (session: AuthSession) => void; theme: "light" | "dark"; onToggleTheme: () => void }) {
  const [mode, setMode] = useState<"signin" | "signup" | "access">("signin");
  const [step, setStep] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passwordSignupEnabled, setPasswordSignupEnabled] = useState(true);
  const [awaitingVerification, setAwaitingVerification] = useState(false);
  const [form, setForm] = useState({
    email: "",
    password: "",
    display_name: "",
    workspace_name: "",
    workspace_id: "",
    access_code: "",
  });

  useEffect(() => {
    void getPasswordSignupStatus()
      .then((status) => setPasswordSignupEnabled(status.enabled))
      .catch(() => setPasswordSignupEnabled(false));
    const verification = new URLSearchParams(window.location.search).get("email_verification");
    if (verification === "expired") setError("This verification link has expired. Request a new one below.");
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "signup" && step === 1) {
      if (!passwordSignupEnabled) return;
      setStep(2);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "access") {
        onAuthenticated(await signInWithWorkspaceCode({
          workspace_id: form.workspace_id,
          code: form.access_code,
          email: form.email,
          display_name: form.display_name,
          password: form.password || null,
        }));
      } else if (mode === "signin") {
        onAuthenticated(await signIn(form.email, form.password));
      } else {
        await signUp(form);
        setAwaitingVerification(true);
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  async function localWorkspace() {
    setBusy(true);
    setError(null);
    try {
      onAuthenticated(await startDevelopmentSession());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Local workspace is unavailable");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: "signin" | "signup" | "access") {
    setMode(next);
    setStep(1);
    setError(null);
    setAwaitingVerification(false);
  }

  async function resend() {
    setBusy(true);
    setError(null);
    try {
      await resendVerification(form.email);
      setError("If the address has a pending account, a new verification link has been sent.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to resend verification");
    } finally {
      setBusy(false);
    }
  }

  function continueWithGoogle() {
    setBusy(true);
    setError(null);
    startGoogleAuth();
  }

  return (
    <main className="auth-shell">
      <div className="auth-ambient" aria-hidden="true">
        <img src={`${BRAND}/twinkle-widecard.svg`} alt="" />
        <span className="auth-orbit orbit-a" />
        <span className="auth-orbit orbit-b" />
        <div className="auth-signal-path"><i /><i /><i /><i /></div>
      </div>
      <section className="auth-story">
        <div className="auth-product-brand"><img className="auth-logo" src={`${BRAND}/corral-mark.png`} alt="" /><span><strong>Corral</strong><small>Powered by Ingenuity Systems</small></span></div>
        <div>
          <span className="eyebrow">AI revenue operations control center</span>
          <h1>Every signal.<br /><em>Understood and acted on.</em></h1>
          <p>One controlled intelligence layer from inbound intent to qualification, routing, notification, and CRM delivery.</p>
        </div>
        <div className="auth-stage-list" aria-label="Lead intelligence stages">
          {['Intake', 'Context', 'Qualification', 'Action'].map((item, index) => <span key={item}><i>{index + 1}</i>{item}</span>)}
        </div>
      </section>
      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-panel-brand">
          <img src={`${BRAND}/corral-mark.png`} alt="" />
          <span>Workspace access</span>
          <button className="theme-toggle auth-theme-toggle" onClick={onToggleTheme} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}><span aria-hidden="true">{theme === "light" ? "◐" : "☼"}</span><span className="theme-label">{theme === "light" ? "Dark" : "Light"}</span></button>
        </div>
        <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
          <button role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "active" : ""} onClick={() => switchMode("signin")}>Sign in</button>
          <button role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "active" : ""} onClick={() => switchMode("signup")}>Create account</button>
          <button role="tab" aria-selected={mode === "access"} className={mode === "access" ? "active" : ""} onClick={() => switchMode("access")}>Staff access</button>
        </div>
        <div className="auth-copy">
          <span className="eyebrow">{mode === "signin" ? "Welcome back" : mode === "access" ? "Admin-issued access" : step === 1 ? "Your identity" : "Workspace setup"}</span>
          <h2 id="auth-title">{mode === "signin" ? "Enter the control center." : mode === "access" ? "Set up staff access." : step === 1 ? "Create your secure account." : "Name your operations workspace."}</h2>
          <p>{mode === "signin" ? "Sign in with your work email and password." : mode === "access" ? "Use the workspace ID and one-time code from your admin. Your work email is your username. New invitees set a password here for future sign-ins; existing accounts keep their current password." : "Workspace ownership keeps leads, analytics, and provider accounts isolated."}</p>
        </div>
        {mode !== "access" && <button type="button" className="google-auth-button" onClick={continueWithGoogle} disabled={busy}>
          <span aria-hidden="true">G</span> Continue with Google
        </button>}
        {mode !== "access" && <div className="auth-divider"><span>or</span></div>}
        {mode === "signup" && !passwordSignupEnabled ? (
          <div className="auth-verification-state" role="status">
            Email signup is temporarily unavailable. Continue with Google to create your workspace.
          </div>
        ) : awaitingVerification ? (
          <div className="auth-verification-state" role="status">
            <strong>Check your email</strong>
            <span>We sent a verification link. Your workspace activates after you verify this address.</span>
            {error && <div className="auth-error" role="alert">{error}</div>}
            <button type="button" className="auth-submit" onClick={() => void resend()} disabled={busy}>Resend verification</button>
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            {mode === "access" && <>
              <label>Workspace ID<input required autoFocus value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" /></label>
              <label>Staff access code<input required type="password" autoComplete="one-time-code" value={form.access_code} onChange={(event) => setForm({ ...form, access_code: event.target.value })} placeholder="Paste the code from your admin" /></label>
              <label>Your work email<input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" /></label>
              <label>Your name<input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" /></label>
            <label>Password for future sign-ins (new accounts)<input minLength={10} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="At least 10 characters" /></label>
            </>}
            {mode === "signup" && step === 1 && <label>Full name<input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" /></label>}
            {(mode === "signin" || (mode === "signup" && step === 1)) && <>
              <label>Work email<input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" /></label>
              <label>Password<input required minLength={mode === "signup" ? 10 : 1} type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="••••••••••" /></label>
            </>}
            {mode === "signup" && step === 2 && <label>Workspace name<input required autoFocus value={form.workspace_name} onChange={(event) => setForm({ ...form, workspace_name: event.target.value })} placeholder="Acme Revenue Operations" /></label>}
            {error && <div className="auth-error" role="alert">{error}</div>}
            <button className="auth-submit" disabled={busy}>{busy ? "Securing session…" : mode === "access" ? "Set up account & enter workspace" : mode === "signin" ? "Sign in" : step === 1 ? "Continue to workspace" : "Create workspace"}</button>
            {mode === "signup" && step === 2 && <button type="button" className="auth-back" onClick={() => setStep(1)}>← Back to account details</button>}
          </form>
        )}
        {import.meta.env.DEV && <>
          <div className="auth-divider"><span>development tools</span></div>
          <button className="demo-session" onClick={() => void localWorkspace()} disabled={busy}>Continue to local workspace</button>
        </>}
        <small className="auth-security">
          <span>Opaque session · HttpOnly cookie</span>
          <span>Workspace membership verified server-side</span>
        </small>
        <nav className="auth-legal-links" aria-label="Legal information">
          <a href="/terms.html">Terms of Service</a>
          <a href="/privacy.html">Privacy Policy</a>
          <a href="mailto:harmainmughal8@gmail.com">Contact</a>
        </nav>
      </section>
    </main>
  );
}
