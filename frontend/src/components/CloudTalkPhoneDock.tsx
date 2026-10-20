import { useEffect, useRef, useState } from "react";

const CLOUDTALK_PHONE_ORIGIN = "https://phone.cloudtalk.io";

const eventMessages: Record<string, { label: string; active?: boolean }> = {
  ringing: { label: "A CloudTalk call is ringing.", active: true },
  dialing: { label: "CloudTalk is dialing.", active: true },
  calling: { label: "CloudTalk call connected.", active: true },
  hangup: { label: "Ending the CloudTalk call…", active: true },
  ended: { label: "CloudTalk call ended.", active: false },
  contact_info: { label: "CloudTalk contact details updated." },
};

function readEventName(value: unknown): string | null {
  let payload = value;
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

export function CloudTalkPhoneDock({
  open,
  onOpen,
  onMinimize,
  onReady,
}: {
  open: boolean;
  onOpen: () => void;
  onMinimize: () => void;
  onReady: () => void;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState(
    "Sign in as the CloudTalk agent selected in Corral before starting a call.",
  );
  const [callActive, setCallActive] = useState(false);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (
        event.origin !== CLOUDTALK_PHONE_ORIGIN ||
        event.source !== frameRef.current?.contentWindow
      ) {
        return;
      }
      const name = readEventName(event.data);
      if (!name) return;
      const update = eventMessages[name];
      if (!update) return;
      setStatus(update.label);
      if (typeof update.active === "boolean") setCallActive(update.active);
    }

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  return (
    <>
      <aside className="cloudtalk-phone-dock" aria-label="CloudTalk Phone">
        <section
          className={`cloudtalk-phone-window${open ? "" : " is-hidden"}`}
          aria-hidden={!open}
        >
          <header className="cloudtalk-phone-header">
            <div>
              <strong>CloudTalk Phone</strong>
              <small>Embedded calling controls</small>
            </div>
            <button
              type="button"
              className="button-quiet"
              onClick={onMinimize}
              disabled={callActive}
              title={callActive ? "End the call before minimizing the phone." : "Minimize CloudTalk Phone"}
            >
              {callActive ? "Call active" : "Minimize"}
            </button>
          </header>
          <p className="cloudtalk-phone-status" role="status" aria-live="polite">
            {status}
          </p>
          <div className="cloudtalk-phone-frame-scroll">
            <iframe
              ref={frameRef}
              className="cloudtalk-phone-frame"
              src={`${CLOUDTALK_PHONE_ORIGIN}?partner=corral`}
              title="CloudTalk Phone"
              allow="microphone https://phone.cloudtalk.io"
              referrerPolicy="strict-origin-when-cross-origin"
              tabIndex={open ? 0 : -1}
              onLoad={onReady}
            />
          </div>
        </section>
      </aside>
      {!open && (
        <div className="cloudtalk-phone-minimized">
          <button type="button" onClick={onOpen}>Open CloudTalk Phone</button>
        </div>
      )}
    </>
  );
}
