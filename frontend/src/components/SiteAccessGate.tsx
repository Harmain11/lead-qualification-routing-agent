import { useState, type FormEvent } from "react";

import { ApiError, unlockSiteAccess } from "../api";

const BRAND = "/asset/brand";

export function SiteAccessGate({
  theme,
  onToggleTheme,
  onUnlocked,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onUnlocked: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unlockSiteAccess(password);
      setPassword("");
      onUnlocked();
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 401
          ? "That password didn’t match. Try again."
          : reason instanceof Error
            ? reason.message
            : "Corral couldn’t verify the password. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="a-shell">
      <button
        type="button"
        className="a-theme"
        onClick={onToggleTheme}
        aria-label={`Switch to ${theme === "light" ? "dark" : "light"} appearance`}
      >
        {theme === "light" ? "Dark" : "Light"}
      </button>
      <section className="a-card" aria-labelledby="site-access-title">
        <img
          className="a-mark"
          src={`${BRAND}/corral-mark.png`}
          alt="Corral"
          width="64"
          height="64"
        />
        <h1 id="site-access-title">Enter Corral</h1>
        <p className="a-lede">
          This site is protected. Enter its access password to continue to sign in.
        </p>
        <form className="a-form" onSubmit={(event) => void submit(event)}>
          <div className="a-fields">
            <label className="a-field">
              <span>Site password</span>
              <input
                required
                autoFocus
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter the Corral access password"
              />
            </label>
          </div>
          {error && <div className="a-error" role="alert">{error}</div>}
          <button className="a-submit" disabled={busy}>
            {busy ? "Checking…" : "Continue"}
          </button>
        </form>
      </section>
    </main>
  );
}
