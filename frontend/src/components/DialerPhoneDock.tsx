import { useEffect, useId, useRef, useState } from "react";
import type AircallWorkspace from "aircall-everywhere";
import {
  isDialerPhoneAvailable,
  ringCentralEmbedClientId,
  type DialerPhoneConnection,
} from "./dialerPhoneConfig";
import "./DialerPhoneDock.css";

const cloudTalkPhoneOrigin = "https://phone.cloudtalk.io";
const providerLabels: Record<string, string> = {
  cloudtalk: "CloudTalk",
  aircall: "Aircall",
  ringcentral: "RingCentral",
};

const cloudTalkEventMessages: Record<
  string,
  { label: string; active?: boolean }
> = {
  ringing: { label: "A CloudTalk call is ringing.", active: true },
  dialing: { label: "CloudTalk is dialing.", active: true },
  calling: { label: "CloudTalk call connected.", active: true },
  hangup: { label: "Ending the CloudTalk call…", active: true },
  ended: { label: "CloudTalk call ended.", active: false },
  contact_info: { label: "CloudTalk contact details updated." },
};

function readCloudTalkEventName(data: unknown): string | null {
  let payload = data;
  if (typeof payload === "string") {
    try {
      payload = JSON.parse(payload) as unknown;
    } catch {
      return null;
    }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const name = (payload as { event?: unknown }).event;
  return typeof name === "string" ? name : null;
}

type BrowserAlertPermission = NotificationPermission | "unsupported";

function getBrowserAlertPermission(): BrowserAlertPermission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

function CloudTalkPhoneFrame({
  onCallActiveChange,
  onIncomingCall,
  onCallEnded,
}: {
  onCallActiveChange: (active: boolean) => void;
  onIncomingCall: () => void;
  onCallEnded: () => void;
}) {
  const frameId = useId();
  const frameViewportRef = useRef<HTMLDivElement>(null);
  const [frameScale, setFrameScale] = useState({ x: 1, y: 1 });
  const [status, setStatus] = useState(
    "Sign in with your CloudTalk account or organization SSO. Your phone session is managed by CloudTalk.",
  );

  useEffect(() => {
    const viewport = frameViewportRef.current;
    if (!viewport) return;
    const updateScale = () => {
      const next = {
        x: Math.min(1, viewport.clientWidth / 420),
        y: Math.min(1, viewport.clientHeight / 700),
      };
      setFrameScale((current) =>
        current.x === next.x && current.y === next.y ? current : next,
      );
    };
    const observer = new ResizeObserver(updateScale);
    observer.observe(viewport);
    updateScale();
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const frame = document.getElementById(frameId) as HTMLIFrameElement | null;
    function handleMessage(event: MessageEvent) {
      if (
        event.origin !== cloudTalkPhoneOrigin ||
        event.source !== frame?.contentWindow
      ) {
        return;
      }
      const name = readCloudTalkEventName(event.data);
      if (!name) return;
      const update = cloudTalkEventMessages[name];
      if (!update) return;
      setStatus(update.label);
      if (typeof update.active === "boolean") onCallActiveChange(update.active);
      if (name === "ringing") onIncomingCall();
      if (name === "ended" || name === "hangup") onCallEnded();
    }

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [frameId, onCallActiveChange, onIncomingCall, onCallEnded]);

  return (
    <>
      <p className="dialer-phone-status dialer-phone-cloudtalk-status" role="status" aria-live="polite">
        {status}
      </p>
      <div className="dialer-phone-cloudtalk-frame-viewport" ref={frameViewportRef}>
        <iframe
          id={frameId}
          className="dialer-phone-frame dialer-phone-frame-cloudtalk"
          style={{ transform: `scale(${frameScale.x}, ${frameScale.y})` }}
          src={`${cloudTalkPhoneOrigin}?partner=corral`}
          title="CloudTalk Phone"
          allow="microphone https://phone.cloudtalk.io"
          referrerPolicy="strict-origin-when-cross-origin"
        />
      </div>
    </>
  );
}

function AircallPhoneFrame({
  onCallActiveChange,
}: {
  onCallActiveChange: (active: boolean) => void;
}) {
  const mountId = `aircall-workspace-${useId().replaceAll(":", "")}`;
  const [status, setStatus] = useState("Loading your Aircall Workspace session…");

  useEffect(() => {
    let cancelled = false;
    let workspace: AircallWorkspace | null = null;
    const events = [
      "incoming_call",
      "outgoing_call",
      "outgoing_answered",
      "call_ended",
      "call_end_ringtone",
    ];
    void import("aircall-everywhere")
      .then(({ default: AircallWorkspaceRuntime }) => {
        if (cancelled) return;
        workspace = new AircallWorkspaceRuntime({
          domToLoadWorkspace: `#${mountId}`,
          size: "auto",
          debug: false,
          onLogin: () => setStatus("Aircall Workspace is ready for this browser."),
          onLogout: () => {
            setStatus("Sign in to your Aircall Workspace account or organization SSO.");
            onCallActiveChange(false);
          },
        });
        for (const event of events.slice(0, 3)) {
          workspace.on(event, () => onCallActiveChange(true));
        }
        for (const event of events.slice(3)) {
          workspace.on(event, () => onCallActiveChange(false));
        }
      })
      .catch(() =>
        setStatus("Aircall Workspace could not load. Check the connection and try again."),
      );
    return () => {
      cancelled = true;
      for (const event of events) workspace?.removeListener(event);
      onCallActiveChange(false);
    };
  }, [mountId, onCallActiveChange]);

  return (
    <>
      <p className="dialer-phone-status" role="status" aria-live="polite">
        {status}
      </p>
      <div id={mountId} className="dialer-phone-aircall-mount" />
    </>
  );
}

export function DialerPhoneDock({
  connection,
  open,
  callActive,
  onToggle,
  onClose,
  onOpen,
  onCallActiveChange,
  notificationPreferenceKey,
}: {
  connection: DialerPhoneConnection | null;
  open: boolean;
  callActive: boolean;
  onToggle: () => void;
  onClose: () => void;
  onOpen: () => void;
  onCallActiveChange: (active: boolean) => void;
  notificationPreferenceKey: string | null;
}) {
  const embedded = isDialerPhoneAvailable(connection);
  const selectedConnection = embedded ? connection : null;
  const provider = selectedConnection?.provider ?? "";
  const providerName = providerLabels[provider] ?? "Dialer";
  const [browserAlertsEnabled, setBrowserAlertsEnabled] = useState(false);
  const [browserAlertPermission, setBrowserAlertPermission] = useState(getBrowserAlertPermission);
  const [browserAlertMessage, setBrowserAlertMessage] = useState("");
  const notificationRef = useRef<Notification | null>(null);

  useEffect(() => {
    const permission = getBrowserAlertPermission();
    setBrowserAlertPermission(permission);
    let savedPreference = false;
    try {
      savedPreference =
        Boolean(notificationPreferenceKey) &&
        window.localStorage.getItem(notificationPreferenceKey!) === "enabled";
    } catch {
      savedPreference = false;
    }
    setBrowserAlertsEnabled(savedPreference && permission === "granted");
    setBrowserAlertMessage("");
  }, [notificationPreferenceKey]);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    function handleServiceWorkerMessage(event: MessageEvent) {
      if (event.data?.type === "corral:open-cloudtalk-phone") onOpen();
    }
    navigator.serviceWorker.addEventListener("message", handleServiceWorkerMessage);
    return () =>
      navigator.serviceWorker.removeEventListener("message", handleServiceWorkerMessage);
  }, [onOpen]);

  async function toggleBrowserAlerts() {
    setBrowserAlertMessage("");
    if (browserAlertsEnabled) {
      setBrowserAlertsEnabled(false);
      try {
        if (notificationPreferenceKey) {
          window.localStorage.removeItem(notificationPreferenceKey);
        }
      } catch {
        // Keep the setting disabled for this session even when storage is unavailable.
      }
      closeIncomingNotification();
      return;
    }

    if (browserAlertPermission === "unsupported") {
      setBrowserAlertMessage("This browser does not support notifications.");
      return;
    }
    if (browserAlertPermission === "denied") {
      setBrowserAlertMessage("Allow notifications for Corral in your browser settings.");
      return;
    }

    try {
      const permission =
        Notification.permission === "granted"
          ? "granted"
          : await Notification.requestPermission();
      setBrowserAlertPermission(permission);
      if (permission !== "granted") {
        setBrowserAlertMessage(
          permission === "denied"
            ? "Allow notifications for Corral in your browser settings."
            : "Notification permission was not granted.",
        );
        return;
      }
      if ("serviceWorker" in navigator) {
        try {
          await navigator.serviceWorker.register("/cloudtalk-notifications-sw.js");
        } catch {
          setBrowserAlertMessage("Background alerts are unavailable; keep Corral open to receive alerts.");
        }
      }
      setBrowserAlertsEnabled(true);
      if (notificationPreferenceKey) {
        try {
          window.localStorage.setItem(notificationPreferenceKey, "enabled");
        } catch {
          // Alerts remain enabled for this session if browser storage is unavailable.
        }
      }
    } catch {
      setBrowserAlertMessage("Could not enable browser alerts. Check this site's permissions.");
    }
  }

  function showIncomingNotification() {
    if (
      !browserAlertsEnabled ||
      typeof Notification === "undefined" ||
      Notification.permission !== "granted"
    ) {
      return;
    }
    const options: NotificationOptions = {
      body: "Your CloudTalk phone is ringing.",
      tag: "corral-cloudtalk-incoming",
    };
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.getRegistration()
        .then((registration) =>
          registration
            ? registration.showNotification("Incoming CloudTalk call", options)
            : showPageNotification(options),
        )
        .catch(() => showPageNotification(options));
      return;
    }
    showPageNotification(options);
  }

  function showPageNotification(options: NotificationOptions) {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    try {
      notificationRef.current?.close();
      const notification = new Notification("Incoming CloudTalk call", options);
      notificationRef.current = notification;
      notification.onclick = () => {
        window.focus();
        onOpen();
        notification.close();
      };
    } catch {
      setBrowserAlertMessage("Browser alerts could not be displayed.");
    }
  }

  function closeIncomingNotification() {
    notificationRef.current?.close();
    notificationRef.current = null;
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.getRegistration()
        .then((registration) =>
          registration?.getNotifications({ tag: "corral-cloudtalk-incoming" }) ?? [],
        )
        .then((notifications) => notifications.forEach((notification) => notification.close()))
        .catch(() => undefined);
    }
  }

  return (
    <aside className="dialer-phone-dock" aria-label="Embedded phone">
      {selectedConnection && (
        <section
          className={`dialer-phone-window${open ? " is-open" : " is-closed"}`}
          aria-hidden={!open}
          aria-label={`${providerName} Phone`}
        >
          <header className="dialer-phone-header">
            <div>
              <strong>{providerName} Phone</strong>
              <small>{selectedConnection?.account_name || "Connected account"}</small>
              {browserAlertMessage && provider === "cloudtalk" && (
                <small className="dialer-phone-alert-message" role="status">
                  {browserAlertMessage}
                </small>
              )}
            </div>
            <div className="dialer-phone-header-actions">
              {provider === "cloudtalk" && (
                <button
                  type="button"
                  className="dialer-phone-alert-toggle"
                  aria-pressed={browserAlertsEnabled}
                  onClick={() => void toggleBrowserAlerts()}
                  title={
                    browserAlertPermission === "denied"
                      ? "Allow notifications for Corral in your browser settings."
                      : "Show a browser alert when CloudTalk reports an incoming call."
                  }
                >
                  {browserAlertPermission === "unsupported"
                    ? "Alerts unavailable"
                    : browserAlertsEnabled
                      ? "Alerts on"
                      : browserAlertPermission === "denied"
                        ? "Alerts blocked"
                        : "Enable alerts"}
                </button>
              )}
              <button
                type="button"
                className="dialer-phone-close"
                onClick={onClose}
                disabled={callActive}
                title={
                  callActive
                    ? "End the active call before closing the phone."
                    : `Close ${providerName} Phone`
                }
                aria-label={`Close ${providerName} Phone`}
              >
                <span aria-hidden="true">×</span>
              </button>
            </div>
          </header>
          <div className="dialer-phone-content">
            {provider === "cloudtalk" && (
              <CloudTalkPhoneFrame
                onCallActiveChange={onCallActiveChange}
                onIncomingCall={showIncomingNotification}
                onCallEnded={closeIncomingNotification}
              />
            )}
            {provider === "aircall" && (
              <div className="dialer-phone-aircall">
                <AircallPhoneFrame onCallActiveChange={onCallActiveChange} />
              </div>
            )}
            {provider === "ringcentral" && (
              <RingCentralPhoneFrame
                clientId={ringCentralEmbedClientId}
                accountName={selectedConnection?.account_name ?? ""}
              />
            )}
          </div>
        </section>
      )}
      {selectedConnection && (
        <button
          type="button"
          className={`dialer-phone-bubble${open ? " is-open" : ""}`}
          onClick={onToggle}
          disabled={open && callActive}
          aria-expanded={open}
          aria-label={`${open ? "Close" : "Open"} ${providerName} Phone`}
          title={
            open && callActive
              ? "End the active call before closing the phone."
              : `${open ? "Close" : "Open"} ${providerName} Phone`
          }
        >
          <span className="dialer-phone-bubble-mark" aria-hidden="true">
            {provider === "cloudtalk" ? "CT" : provider === "ringcentral" ? "RC" : "A"}
          </span>
          <span>{providerName}</span>
          {callActive && <i aria-label="Call active" />}
        </button>
      )}
    </aside>
  );
}

function RingCentralPhoneFrame({
  clientId,
  accountName,
}: {
  clientId: string;
  accountName: string;
}) {
  if (!clientId) {
    return (
      <div className="dialer-phone-setup" role="status">
        <strong>RingCentral embed setup required</strong>
        <p>
          Register a RingCentral Embeddable app with 3-legged OAuth and Voice
          permissions. Each member signs in with their own RingCentral account.
          Then set the public app ID as
          <code> VITE_RINGCENTRAL_EMBED_CLIENT_ID</code> before building.
        </p>
        <small>{accountName}</small>
      </div>
    );
  }

  const source = new URL(
    "https://apps.ringcentral.com/integration/ringcentral-embeddable/latest/app.html",
  );
  source.searchParams.set("clientId", clientId);
  source.searchParams.set("appServer", "https://platform.ringcentral.com");

  return (
    <>
      <p className="dialer-phone-status" role="status" aria-live="polite">
        Sign in with your RingCentral account or organization SSO. RingCentral manages this browser session.
      </p>
      <iframe
        className="dialer-phone-frame"
        src={source.toString()}
        title="RingCentral Phone"
        allow="microphone; autoplay; clipboard-read; clipboard-write"
        referrerPolicy="strict-origin-when-cross-origin"
      />
    </>
  );
}
