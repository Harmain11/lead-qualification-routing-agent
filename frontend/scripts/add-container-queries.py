"""Mirror page-content breakpoints as container queries on the page area.

Most layout rules were written as `@media (max-width: N px)`, i.e. against
the screen. The page area is the screen minus the sidebar (248px), and it
shrinks further when Ask Corral opens. This adds, right after each such media
block, `@container page (max-width: N-248 px)` with the same content rules, so
narrow layouts kick in whenever the *page* is narrow.

Additive by design: with the sidebar shown and Ask Corral closed the page is
exactly screen − 248px, so both conditions agree and nothing changes. Rules for
things outside the page area or floating above it (dialogs, sheets, menus,
the sidebar, sign-in, Ask Corral itself) are left screen-based.

Usage: python3 add-container-queries.py [--write] CSS_FILE...
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SIDEBAR = 248
MARK = "/* page-container mirror */"
QUERY = re.compile(r"^\s*@media\s*(?:screen\s+and\s*)?\(\s*max-width\s*:\s*(\d+)px\s*\)\s*$")
# Selectors that are not laid out inside the page area.
OUTSIDE = re.compile(
    r"dialog|sheet|modal|palette|popover|tour|flyout|menu|toast|tooltip|"
    r"v-sidebar|v-tabbar|v-more|corral-agent|lead-agent|a-shell|a-card|auth|"
    r"html|body|:root|\.v-app\[|\.app-shell\[|v-window\b|v-toolbar\b(?![-\w])|workspace-switcher|"
    r"notification-inbox|select-menu|v-skip|dialer-phone",
    re.I,
)


def block_end(css: str, start: int) -> int:
    depth, index = 0, start
    while index < len(css):
        if css.startswith("/*", index):
            close = css.find("*/", index + 2)
            index = len(css) if close < 0 else close + 2
            continue
        char = css[index]
        if char in "\"'":
            close = css.find(char, index + 1)
            index = len(css) if close < 0 else close + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(css)


def content_rules(inner: str) -> str:
    """Keep only plain rules whose selectors are all page content."""
    kept = []
    bare = re.sub(r"/\*.*?\*/", "", inner, flags=re.S)
    index = 0
    while True:
        brace = bare.find("{", index)
        if brace < 0:
            break
        selector = bare[index:brace].strip()
        end = block_end(bare, brace)
        if selector and not selector.startswith("@"):
            parts = [part.strip() for part in selector.split(",")]
            inside = [part for part in parts if not OUTSIDE.search(part)]
            if inside:
                kept.append(f"{', '.join(inside)} {bare[brace:end]}")
        index = end
    return "\n".join(kept)


def strip_mirrors(css: str) -> str:
    """Remove blocks this script added earlier so it can regenerate them."""
    while True:
        start = css.find(MARK)
        if start < 0:
            return css
        brace = css.find("{", start)
        end = block_end(css, brace)
        css = css[:start].rstrip("\n") + css[end:]


def mirror(css: str) -> tuple[str, int]:
    css = strip_mirrors(css)
    out, index, added = [], 0, 0
    while True:
        match = re.compile(r"@media[^{;]*\{").search(css, index)
        if not match:
            out.append(css[index:])
            break
        brace = match.end() - 1
        end = block_end(css, brace)
        out.append(css[index:end])
        query = QUERY.match(css[match.start():brace])
        if query and int(query.group(1)) >= 768:
            rules = content_rules(css[brace + 1:end - 1])
            if rules.strip():
                width = int(query.group(1)) - SIDEBAR
                out.append(f"\n{MARK}\n@container page (max-width: {width}px) {{\n{rules}\n}}")
                added += 1
        index = end
    return "".join(out), added


if __name__ == "__main__":
    write = "--write" in sys.argv
    total = 0
    for name in [arg for arg in sys.argv[1:] if arg != "--write"]:
        path = Path(name)
        css = path.read_text(encoding="utf-8")
        updated, added = mirror(css)
        if updated != css:
            total += added
            print(f"{added:4d}  {path}")
            if write:
                path.write_text(updated, encoding="utf-8")
    print(f"{total:4d}  container blocks {'added' if write else 'to add'}")
