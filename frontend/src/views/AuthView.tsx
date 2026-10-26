import type React from "react";
import type { FormEvent } from "react";
import { useEffect,useState } from "react";

import { getPasswordSignupStatus,resendVerification,signIn,signInWithWorkspaceCode,signUp,startDevelopmentSession,startGoogleAuth } from "../api";
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

  const heading = mode === "signin"
    ? "Sign in to Corral"
    : mode === "access"
      ? "Join your team’s workspace"
      : step === 1 ? "Create your account" : "Name your workspace";
  const lede = mode === "signin"
    ? "Every lead, understood and ready for your team."
    : mode === "access"
      ? "Use the workspace ID and one-time code from your admin. Your work email becomes your username; new invitees choose a password here."
      : step === 1 ? "Leads, analytics and connections stay private to each workspace." : "You can rename it later in Settings.";
  const field = (label: string, input: React.ReactNode) => <label className="a-field"><span>{label}</span>{input}</label>;
  return (
    <main className="a-shell">
      <button type="button" className="a-theme" onClick={onToggleTheme} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} appearance`}>
        {theme === "light" ? "Dark" : "Light"}
      </button>
      <section className="a-card" aria-labelledby="auth-title">
        <img className="a-mark" src={`${BRAND}/corral-mark.png`} alt="Corral" width="64" height="64" />
        <h1 id="auth-title">{heading}</h1>
        <p className="a-lede">{lede}</p>

        <div className="a-modes" role="tablist" aria-label="How you’re signing in" style={{ "--a-index": mode === "signin" ? 0 : mode === "signup" ? 1 : 2 } as React.CSSProperties}>
          <span className="a-modes-thumb" aria-hidden="true" />
          <button role="tab" type="button" aria-selected={mode === "signin"} onClick={() => switchMode("signin")}>Sign In</button>
          <button role="tab" type="button" aria-selected={mode === "signup"} onClick={() => switchMode("signup")}>New Account</button>
          <button role="tab" type="button" aria-selected={mode === "access"} onClick={() => switchMode("access")}>Team Code</button>
        </div>

        {mode !== "access" && (
          <>
            <button type="button" className="a-google" onClick={continueWithGoogle} disabled={busy}>
              <img src={`${BRAND}/google-g.png`} width="18" height="18" alt="" aria-hidden="true" />
              Continue with Google
            </button>
            <div className="a-or"><span>or</span></div>
          </>
        )}

        {mode === "signup" && !passwordSignupEnabled ? (
          <div className="a-state" role="status">Email sign-up is unavailable right now. Continue with Google to create your workspace.</div>
        ) : awaitingVerification ? (
          <div className="a-state" role="status">
            <b>Check your email</b>
            <span>We sent a verification link. Your workspace opens once you’ve confirmed this address.</span>
            {error && <div className="a-error" role="alert">{error}</div>}
            <button type="button" className="a-submit" onClick={() => void resend()} disabled={busy}>Resend Link</button>
          </div>
        ) : (
          <form className="a-form" onSubmit={submit}>
            <div className="a-fields">
              {mode === "access" && <>
                {field("Workspace ID", <input required autoFocus value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />)}
                {field("Access code", <input required type="password" autoComplete="one-time-code" value={form.access_code} onChange={(event) => setForm({ ...form, access_code: event.target.value })} placeholder="From your admin" />)}
                {field("Work email", <input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" />)}
                {field("Your name", <input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" />)}
                {field("Password for next time", <input minLength={10} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="At least 10 characters" />)}
              </>}
              {mode === "signup" && step === 1 && field("Full name", <input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" />)}
              {(mode === "signin" || (mode === "signup" && step === 1)) && <>
                {field("Work email", <input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" />)}
                {field("Password", <input required minLength={mode === "signup" ? 10 : 1} type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={mode === "signup" ? "At least 10 characters" : "Password"} />)}
              </>}
              {mode === "signup" && step === 2 && field("Workspace name", <input required autoFocus value={form.workspace_name} onChange={(event) => setForm({ ...form, workspace_name: event.target.value })} placeholder="Acme Revenue" />)}
            </div>
            {error && <div className="a-error" role="alert">{error}</div>}
            <button className="a-submit" disabled={busy}>{busy ? "Signing in…" : mode === "access" ? "Join Workspace" : mode === "signin" ? "Sign In" : step === 1 ? "Continue" : "Create Workspace"}</button>
            {mode === "signup" && step === 2 && <button type="button" className="a-link" onClick={() => setStep(1)}>Back</button>}
          </form>
        )}
        {import.meta.env.DEV && (
          <button className="a-link a-dev" type="button" onClick={() => void localWorkspace()} disabled={busy}>Continue to local workspace</button>
        )}
      </section>
      <footer className="a-foot">
        <span>Sessions use a secure HttpOnly cookie; workspace access is checked on the server.</span>
        <nav aria-label="Legal information">
          <a href="/terms.html">Terms</a>
          <a href="/privacy.html">Privacy</a>
          <a href="mailto:harmainmughal8@gmail.com">Contact</a>
        </nav>
        <span>Corral by Ingenuity Systems</span>
      </footer>
    </main>
  );
}
