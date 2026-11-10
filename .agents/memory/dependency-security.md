---
name: Dependency security constraints
description: Preserve security fixes across backend installation paths.
---

Keep patched minimum versions in the backend manifest, including security-sensitive transitive dependencies, rather than changing only the lockfile.

**Why:** Production and post-merge setup install the backend with pip, which does not use the uv lockfile. A lockfile-only security fix would not constrain those installations.

**How to apply:** When remediating dependencies, update the manifest's minimum version and its lockfile together. Check major upgrades against the application tests.

Preserve public npm tarball URLs in lockfiles used by external deployment and integration packaging.

**Why:** The local registry can write Replit-only download URLs during updates, which makes otherwise valid lockfiles unusable outside this environment.

**How to apply:** Check updated lockfile URLs before completion, retaining verified versions and integrity hashes when normalizing URLs. Do not change registry routing or bypass package security checks for installation.

The managed Python installer can create an unrelated root Python project when the real manifest is nested.

**Why:** Installing packages in this environment created root scaffolding instead of using the existing backend project.

**How to apply:** Inspect installer-generated files and retain the backend as the single authoritative Python project; do not commit unrelated generated scaffolding.
