export type DialerPhoneConnection = {
  id: string;
  provider: string;
  account_name: string;
};

export const ringCentralEmbedClientId =
  (import.meta.env as Record<string, string | undefined>)
    .VITE_RINGCENTRAL_EMBED_CLIENT_ID?.trim() ?? "";

export function isEmbeddedDialerConnection(
  connection: Pick<DialerPhoneConnection, "provider"> | null | undefined,
) {
  if (!connection) return false;
  return ["cloudtalk", "aircall", "ringcentral"].includes(connection.provider);
}
