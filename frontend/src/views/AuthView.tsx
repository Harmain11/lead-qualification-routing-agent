import { useState } from "react";
import type { FormEvent } from "react";

import { signIn, signUp, startDevelopmentSession } from "../api";
import type { AuthSession } from "../types";

const BRAND = "/asset/brand";

export function AuthView({ onAuthenticated }: { onAuthenticated: (session: AuthSession) => void }) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [step, setStep] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    email: "",
    password: "",
    display_name: "",
    workspace_name: "",
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "signup" && step === 1) {
      setStep(2);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const session = mode === "signin"
        ? await signIn(form.email, form.password)
        : await signUp(form);
      onAuthenticated(session);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  async function localDemo() {
    setBusy(true);
    setError(null);
    try {
      onAuthenticated(await startDevelopmentSession());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Local demo is unavailable");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: "signin" | "signup") {
    setMode(next);
    setStep(1);
    setError(null);
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
        <img className="auth-logo" src={`${BRAND}/logo-light.png`} alt="Ingenuity Systems" />
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
          <img src={`${BRAND}/logo-icon.png`} alt="" />
          <span>Secure workspace access</span>
        </div>
        <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
          <button role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "active" : ""} onClick={() => switchMode("signin")}>Sign in</button>
          <button role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "active" : ""} onClick={() => switchMode("signup")}>Create account</button>
        </div>
        <div className="auth-copy">
          <span className="eyebrow">{mode === "signin" ? "Welcome back" : step === 1 ? "Your identity" : "Workspace setup"}</span>
          <h2 id="auth-title">{mode === "signin" ? "Enter the control center." : step === 1 ? "Create your secure account." : "Name your operations workspace."}</h2>
          <p>{mode === "signin" ? "Your session resolves only the workspaces you belong to." : "Workspace ownership keeps leads, analytics, and provider accounts isolated."}</p>
        </div>
        <form className="auth-form" onSubmit={submit}>
          {mode === "signup" && step === 1 && <label>Full name<input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" /></label>}
          {(mode === "signin" || step === 1) && <>
            <label>Work email<input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" /></label>
            <label>Password<input required minLength={mode === "signup" ? 10 : 1} type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="••••••••••" /></label>
          </>}
          {mode === "signup" && step === 2 && <label>Workspace name<input required autoFocus value={form.workspace_name} onChange={(event) => setForm({ ...form, workspace_name: event.target.value })} placeholder="Acme Revenue Operations" /></label>}
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="auth-submit" disabled={busy}>{busy ? "Securing session…" : mode === "signin" ? "Sign in securely" : step === 1 ? "Continue to workspace" : "Create workspace"}</button>
          {mode === "signup" && step === 2 && <button type="button" className="auth-back" onClick={() => setStep(1)}>← Back to account details</button>}
        </form>
        <div className="auth-divider"><span>local development</span></div>
        <button className="demo-session" onClick={() => void localDemo()} disabled={busy}>Continue to seeded demo workspace</button>
        <small className="auth-security">Opaque session · HttpOnly cookie · Workspace membership verified server-side</small>
      </section>
    </main>
  );
}
