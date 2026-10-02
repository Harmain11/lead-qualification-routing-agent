---
name: OpenAI credential prompts
description: Product behavior for when setup should request a workspace OpenAI API key again.
---

Once an OpenAI key is stored for a workspace, do not show the setup prompt again merely because connection verification is pending. Treat key presence and verification as separate states. Prompt again only when no stored usable credential exists, such as when it is missing, disconnected, or invalid; surface verification issues in Integration settings rather than repeatedly asking for the key.

**Why:** The user asked not to be asked for permission or a key repeatedly after adding it.

**How to apply:** Gate setup prompts on the safe stored-credential indicator, not only on verified connection status. Never read or expose the secret to determine whether it exists.