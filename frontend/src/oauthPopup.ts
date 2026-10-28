export const OAUTH_RESULT_CHANNEL = "corral-oauth-result";

export type OAuthPopupResult = {
  type: "corral-oauth-result";
  provider: string;
  status: "connected" | "failed";
  message?: string;
};

export function isOAuthPopupResult(value: unknown): value is OAuthPopupResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<OAuthPopupResult>;
  return result.type === "corral-oauth-result"
    && typeof result.provider === "string"
    && (result.status === "connected" || result.status === "failed")
    && (result.message === undefined || typeof result.message === "string");
}

export function relayOAuthCallbackFromPopup() {
  const params = new URLSearchParams(window.location.search);
  const provider = params.get("integration");
  const status = params.get("status");
  if (!provider || (status !== "connected" && status !== "failed")) return;

  const result: OAuthPopupResult = {
    type: "corral-oauth-result",
    provider,
    status,
    message: params.get("message")?.trim().slice(0, 500) || undefined,
  };

  if (window.name.startsWith("corral-oauth-") && "BroadcastChannel" in window) {
    const channel = new BroadcastChannel(OAUTH_RESULT_CHANNEL);
    channel.postMessage(result);
    channel.close();
  } else if (window.opener) {
    window.opener.postMessage(result, window.location.origin);
  }
}
