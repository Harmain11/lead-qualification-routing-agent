import type React from "react";
import type { FormEvent } from "react";
import { useEffect, useState } from "react";

import {
  confirmPasswordReset,
  getPasswordSignupStatus,
  requestPasswordReset,
  resendVerification,
  signIn,
  signInWithWorkspaceCode,
  signUp,
  startDevelopmentSession,
  startGoogleAuth,
} from "../api";
import type { AuthSession } from "../types";
import { SUPPORT_EMAIL } from "../brand";

const BRAND = "/asset/brand";

type Mode = "signin" | "signup" | "access";
// "forgot" asks for an email, "reset" sets a new password from an emailed link.
type Flow = "form" | "verify" | "forgot" | "forgot-sent" | "reset" | "reset-done";

// Kept in memory only, so repeated reads (React may call initializers twice) agree.
let pendingResetToken: string | null = null;

/** Reads a reset token from the link once, then removes it from the address bar and history. */
function takeResetToken(): string {
  if (pendingResetToken !== null) return pendingResetToken;
  const url = new URL(window.location.href);
  pendingResetToken = url.searchParams.get("reset_password") ?? "";
  if (pendingResetToken) {
    url.searchParams.delete("reset_password");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return pendingResetToken;
}

export function AuthView({ onAuthenticated, theme, onToggleTheme }: { onAuthenticated: (session: AuthSession) => void; theme: "light" | "dark"; onToggleTheme: () => void }) {
  const [resetToken] = useState(takeResetToken);
  const [mode, setMode] = useState<Mode>("signin");
  const [flow, setFlow] = useState<Flow>(resetToken ? "reset" : "form");
  const [step, setStep] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [passwordSignupEnabled, setPasswordSignupEnabled] = useState(true);
  const [newPassword, setNewPassword] = useState({ password: "", confirm: "" });
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

  async function run(task: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await task();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : fallback);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "signup" && step === 1) {
      if (!passwordSignupEnabled) return;
      setStep(2);
      return;
    }
    await run(async () => {
      if (mode === "access") {
        onAuthenticated(await signInWithWorkspaceCode({
          workspace_id: form.workspace_id.trim(),
          code: form.access_code,
          email: form.email,
          display_name: form.display_name,
          password: form.password || null,
        }));
      } else if (mode === "signin") {
        onAuthenticated(await signIn(form.email, form.password));
      } else {
        await signUp(form);
        setFlow("verify");
      }
    }, "Something went wrong. Try again.");
  }

  async function submitForgot(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await requestPasswordReset(form.email);
      setFlow("forgot-sent");
    }, "The reset link couldn’t be requested. Try again.");
  }

  async function submitReset(event: FormEvent) {
    event.preventDefault();
    if (newPassword.password !== newPassword.confirm) {
      setError("The two passwords don’t match.");
      return;
    }
    await run(async () => {
      await confirmPasswordReset(resetToken, newPassword.password);
      pendingResetToken = "";
      setNewPassword({ password: "", confirm: "" });
      setFlow("reset-done");
    }, "Your password couldn’t be updated. Try again.");
  }

  function switchMode(next: Mode) {
    setMode(next);
    setStep(1);
    setError(null);
    setNotice(null);
    setFlow("form");
  }

  function backToSignIn() {
    setMode("signin");
    setStep(1);
    setError(null);
    setNotice(null);
    setFlow("form");
  }

  const resend = () => run(async () => {
    await resendVerification(form.email);
    setNotice("If the address has a pending account, a new link is on its way.");
  }, "The link couldn’t be resent. Try again.");

  const localWorkspace = () => run(async () => {
    onAuthenticated(await startDevelopmentSession());
  }, "The local workspace is unavailable.");

  function continueWithGoogle() {
    setBusy(true);
    setError(null);
    startGoogleAuth();
  }

  const isRecovery = flow === "forgot" || flow === "forgot-sent" || flow === "reset" || flow === "reset-done";
  const heading = flow === "forgot" || flow === "forgot-sent"
    ? "Reset your password"
    : flow === "reset" ? "Choose a new password"
    : flow === "reset-done" ? "Password updated"
    : mode === "signin" ? "Sign in to Corral"
    : mode === "access" ? "Join your team’s workspace"
    : step === 1 ? "Create your account" : "Name your workspace";
  const lede = flow === "forgot"
    ? "Enter the email you sign in with and we’ll send a link to choose a new password."
    : flow === "forgot-sent" ? "The link works once and expires after 30 minutes."
    : flow === "reset" ? "Use at least 10 characters. You’ll be signed out on every other device."
    : flow === "reset-done" ? "Sign in with your new password. Other devices have been signed out."
    : mode === "signin" ? "Every lead, understood and ready for your team."
    : mode === "access" ? "Use the workspace ID and one-time code from your admin. New teammates choose a password here."
    : step === 1 ? "Leads, analytics and connections stay private to each workspace." : "You can rename it later in Settings.";
  const submitLabel = busy
    ? mode === "access" ? "Joining…" : mode === "signin" ? "Signing in…" : step === 1 ? "Continue" : "Creating…"
    : mode === "access" ? "Join workspace" : mode === "signin" ? "Sign in" : step === 1 ? "Continue" : "Create workspace";
  const field = (label: string, input: React.ReactNode) => <label className="a-field"><span>{label}</span>{input}</label>;
  const feedback = <>
    {error && <div className="a-error" role="alert">{error}</div>}
    {notice && <div className="a-notice" role="status">{notice}</div>}
  </>;

  return (
    <main className="a-shell">
      <button type="button" className="a-theme" onClick={onToggleTheme} aria-label={`Switch to ${theme === "light" ? "dark" : "light"} appearance`}>
        {theme === "light" ? "Dark" : "Light"}
      </button>
      <section className="a-card" aria-labelledby="auth-title">
        <img className="a-mark" src={`${BRAND}/corral-mark.png`} alt="Corral" width="64" height="64" />
        <h1 id="auth-title">{heading}</h1>
        <p className="a-lede">{lede}</p>

        {isRecovery ? (
          <>
            {flow === "forgot" && (
              <form className="a-form" onSubmit={submitForgot}>
                <div className="a-fields">
                  {field("Work email", <input required autoFocus type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" />)}
                </div>
                {feedback}
                <button className="a-submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
              </form>
            )}
            {flow === "forgot-sent" && (
              <div className="a-state" role="status">
                <b>If an account uses {form.email || "that address"}, a reset link is on its way.</b>
                <span>Didn’t get it? Check spam, or try again in a minute.</span>
                {feedback}
                <button type="button" className="a-submit" disabled={busy} onClick={() => void run(async () => {
                  await requestPasswordReset(form.email);
                  setNotice("If an account uses that address, another link is on its way.");
                }, "The link couldn’t be resent. Try again.")}>{busy ? "Sending…" : "Send again"}</button>
              </div>
            )}
            {flow === "reset" && (
              <form className="a-form" onSubmit={submitReset}>
                <div className="a-fields">
                  {field("New password", <input required autoFocus minLength={10} type="password" autoComplete="new-password" value={newPassword.password} onChange={(event) => setNewPassword({ ...newPassword, password: event.target.value })} placeholder="At least 10 characters" />)}
                  {field("Confirm new password", <input required minLength={10} type="password" autoComplete="new-password" value={newPassword.confirm} onChange={(event) => setNewPassword({ ...newPassword, confirm: event.target.value })} placeholder="Type it again" />)}
                </div>
                {feedback}
                <button className="a-submit" disabled={busy}>{busy ? "Saving…" : "Save new password"}</button>
              </form>
            )}
            {flow === "reset-done" && (
              <button type="button" className="a-submit" onClick={backToSignIn}>Sign in</button>
            )}
            {flow !== "reset-done" && (
              <button type="button" className="a-link" onClick={backToSignIn}>Back to sign in</button>
            )}
          </>
        ) : (
          <>
            <div className="a-modes" role="tablist" aria-label="How you’re signing in" style={{ "--a-index": mode === "signin" ? 0 : mode === "signup" ? 1 : 2 } as React.CSSProperties}>
              <span className="a-modes-thumb" aria-hidden="true" />
              <button role="tab" type="button" aria-selected={mode === "signin"} onClick={() => switchMode("signin")}>Sign in</button>
              <button role="tab" type="button" aria-selected={mode === "signup"} onClick={() => switchMode("signup")}>New account</button>
              <button role="tab" type="button" aria-selected={mode === "access"} onClick={() => switchMode("access")}>Team code</button>
            </div>

            {mode !== "access" && flow === "form" && (
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
            ) : flow === "verify" ? (
              <div className="a-state" role="status">
                <b>Check your email</b>
                <span>We sent a verification link to {form.email}. Your workspace opens once you’ve confirmed it.</span>
                {feedback}
                <button type="button" className="a-submit" onClick={() => void resend()} disabled={busy}>{busy ? "Sending…" : "Resend link"}</button>
              </div>
            ) : (
              <form className="a-form" onSubmit={submit}>
                <div className="a-fields">
                  {mode === "access" && <>
                    {field("Workspace ID", <input required autoFocus spellCheck={false} value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />)}
                    {field("Access code", <input required type="password" autoComplete="one-time-code" value={form.access_code} onChange={(event) => setForm({ ...form, access_code: event.target.value })} placeholder="From your admin" />)}
                    {field("Work email", <input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" />)}
                    {field("Your name", <input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" />)}
                    {field("Choose a password", <input minLength={10} type="password" autoComplete="new-password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="At least 10 characters" />)}
                    <p className="a-hint">Already have a password for this workspace? Leave it blank.</p>
                  </>}
                  {mode === "signup" && step === 1 && field("Full name", <input required autoComplete="name" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Alex Morgan" />)}
                  {(mode === "signin" || (mode === "signup" && step === 1)) && <>
                    {field("Work email", <input required type="email" autoComplete="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="alex@company.com" />)}
                    {field("Password", <input required minLength={mode === "signup" ? 10 : 1} type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={mode === "signup" ? "At least 10 characters" : "Password"} />)}
                  </>}
                  {mode === "signin" && (
                    <button type="button" className="a-link a-forgot" onClick={() => { setError(null); setNotice(null); setFlow("forgot"); }}>
                      Forgot password?
                    </button>
                  )}
                  {mode === "signup" && step === 2 && field("Workspace name", <input required autoFocus value={form.workspace_name} onChange={(event) => setForm({ ...form, workspace_name: event.target.value })} placeholder="Acme Revenue" />)}
                </div>
                {feedback}
                <button className="a-submit" disabled={busy}>{submitLabel}</button>
                {mode === "signup" && step === 2 && <button type="button" className="a-link" onClick={() => setStep(1)}>Back</button>}
              </form>
            )}
          </>
        )}
        {import.meta.env.DEV && flow === "form" && (
          <button className="a-link a-dev" type="button" onClick={() => void localWorkspace()} disabled={busy}>Continue to local workspace</button>
        )}
      </section>
      <footer className="a-foot">
        <span>Sessions use a secure HttpOnly cookie; workspace access is checked on the server.</span>
        <nav aria-label="Legal information">
          <a href="/terms.html">Terms</a>
          <a href="/privacy.html">Privacy</a>
          <a href={`mailto:${SUPPORT_EMAIL}`}>Contact</a>
        </nav>
        <span>Corral by Ingenuity Systems</span>
      </footer>
    </main>
  );
}
