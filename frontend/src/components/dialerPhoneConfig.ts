export type DialerPhoneConnection = {
  id: string;
  provider: string;
  account_name: string;
  status: string;
  is_active: boolean;
};

export const OPEN_DIALER_PHONE_EVENT = "corral:open-dialer-phone";

export const ringCentralEmbedClientId =
  (
    import.meta as ImportMeta & {
      env?: Record<string, string | undefined>;
    }
  ).env?.VITE_RINGCENTRAL_EMBED_CLIENT_ID?.trim() ?? "";

export function isEmbeddedDialerConnection(
  connection: Pick<DialerPhoneConnection, "provider"> | null | undefined,
) {
  if (!connection) return false;
  return ["cloudtalk", "aircall", "ringcentral"].includes(connection.provider);
}

export function isDialerPhoneAvailable(
  connection: DialerPhoneConnection | null | undefined,
) {
  return Boolean(
    connection &&
      connection.is_active &&
      connection.status === "verified" &&
      isEmbeddedDialerConnection(connection),
  );
}
