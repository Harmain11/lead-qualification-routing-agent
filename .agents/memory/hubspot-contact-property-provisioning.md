---
name: HubSpot contact property provisioning
description: Durable rules for provisioning mapped HubSpot Contact properties during outbound sync.
---

Before an outbound HubSpot Contact sync, inspect mapped Contact property definitions. Create only missing properties; never overwrite or delete existing definitions. If an existing property's data type conflicts with the mapped Corral value, stop with a clear validation error. Keep this behavior limited to Contact properties unless the user asks to include other object types.

**Why:** The user approved automatic creation to avoid repeated manual setup while preserving existing HubSpot configuration.

**How to apply:** Request the minimum contact-schema permissions, make provisioning idempotent, and require existing HubSpot OAuth connections to reconnect after scope changes. Treat optional Company/Deal provisioning as separate scope.
