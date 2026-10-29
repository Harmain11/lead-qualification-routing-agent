"""One-off codemod: replace legacy design tokens with v3 tokens, choosing by CSS property."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"

DIRECT = {
    "muted": "label-2", "text": "label", "subtle": "label-3",
    "line": "separator-strong", "line-bright": "separator-strong",
    "surface-high": "surface-sunken", "surface-solid": "surface", "surface-soft": "tint-soft",
    "orange-soft": "tint-soft", "blue-soft": "tint-soft", "blue-line": "tint-soft-strong",
    "blue-deep": "label", "radius-sm": "r-sm", "radius": "r-md", "radius-lg": "r-lg",
    "shadow": "shadow-card", "ease": "ease-out", "fast": "d-fast", "normal": "d-slow",
    "space-1": "s1", "space-2": "s2", "space-3": "s3", "space-4": "s4", "space-6": "s6", "space-8": "s8",
    "control-height": "control-h-lg", "card-padding": "s6",
    "font-body": "fs-body", "font-control": "fs-callout", "font-label": "fs-callout", "font-caption": "fs-subhead",
    "page-max": "v-page-max", "page-gutter": "page-pad", "section-gap": "s6",
    "bg": "canvas", "bg-deep": "canvas", "navy": "sidebar", "navy-high": "fill-hover",
    "brand-coral": "attention", "brand-navy": "c-navy", "brand-ink": "ink-night", "brand-off-white": "paper",
}
ACCENT = {"orange", "orange-hot", "brand-orange", "green", "blue", "blue-mid"}
BY_ROLE = {  # token -> (as text colour, as fill/border)
    **{name: ("tint-text", "tint") for name in ACCENT},
    "red": ("danger-text", "danger"),
    "amber": ("attention-text", "attention"),
}
TOKEN = re.compile(r"var\(--([a-z0-9-]+)(?:\s*,\s*[^()]*(?:\([^()]*\)[^()]*)*)?\)")
DECL = re.compile(r"(?P<prop>[a-zA-Z-]+)\s*:\s*(?P<value>[^;{}]+)")


def swap(value: str, is_text: bool) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in BY_ROLE:
            return f"var(--{BY_ROLE[name][0 if is_text else 1]})"
        if name in DIRECT:
            return f"var(--{DIRECT[name]})"
        return match.group(0)
    return TOKEN.sub(repl, value)


def migrate_css(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if "var(--" not in value:
            return match.group(0)
        prop = match.group("prop").lower()
        is_text = prop in {"color", "caret-color", "-webkit-text-fill-color", "text-decoration-color"}
        start = match.start("value") - match.start()
        return match.group(0)[:start] + swap(value, is_text)
    return DECL.sub(repl, text)


def migrate_tsx(text: str) -> str:
    # Inline styles: `color: "var(--x)"` is text; everything else is treated as a fill.
    text = re.sub(r"(color\s*:\s*[\"'`])(var\(--[a-z0-9-]+\))",
                  lambda m: m.group(1) + swap(m.group(2), True), text)
    return TOKEN.sub(lambda m: swap(m.group(0), False), text)


def main() -> int:
    changed = 0
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in {".css", ".tsx", ".ts"} or path.name == "designTokens.css":
            continue
        source = path.read_text(encoding="utf8")
        result = migrate_css(source) if path.suffix == ".css" else migrate_tsx(source)
        if result != source:
            path.write_text(result, encoding="utf8")
            changed += 1
    print(f"files changed: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
