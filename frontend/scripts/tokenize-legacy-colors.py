"""Rewrite hard-coded light-theme colours in legacy stylesheets to v3 tokens.

Legacy rules were written for a white page. In dark mode their literal
whites, pale greys and dark text stay put and produce white boxes with
white text. This maps each literal by property and lightness:

  background  white            -> --surface
              pale (L > .90)   -> --surface-sunken
              brand green      -> --tint
  color       dark (L < .30)   -> --label
              mid grey         -> --label-2 / --label-3
              brand green      -> --tint-text
              strong red       -> --danger-text
  border      brand green      -> --tint
              pale (L > .85)   -> --separator-strong

Rules under a dark-theme selector or a prefers-color-scheme: dark media
block are left alone, as are white text and knobs (they sit on filled
controls in both themes). Usage: python3 tokenize-legacy-colors.py FILE...
"""
from __future__ import annotations

import re
import sys

BRAND_GREENS = {"#1e6b52", "#155640", "#216c52", "#277e62", "#17573f", "#1a5c45"}
STRONG_REDS = {"#a3332e", "#a62e16", "#b42318", "#b3261e", "#9f2d20"}
DECL = re.compile(r"(?<![-\w])(background-color|background|color|border-color|border(?:-(?:top|right|bottom|left))?)(\s*:\s*)([^;{}]*)")
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\bwhite\b")


def lightness(value: str) -> float:
    if value == "white":
        return 1.0
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (max(r, g, b) + min(r, g, b)) / 2


def token(prop: str, literal: str) -> str | None:
    low = literal.lower()
    light = lightness(low)
    if prop.startswith("background"):
        if low in BRAND_GREENS:
            return "var(--tint)"
        if low in {"#fff", "#ffffff", "white"}:
            return "var(--surface)"
        if light > 0.90:
            return "var(--surface-sunken)"
        return None
    if prop == "color":
        if low in {"#fff", "#ffffff", "white"}:
            return None
        if low in BRAND_GREENS:
            return "var(--tint-text)"
        if low in STRONG_REDS:
            return "var(--danger-text)"
        if light < 0.30:
            return "var(--label)"
        if 0.30 <= light < 0.45:
            return "var(--label-2)"
        if 0.45 <= light < 0.62:
            return "var(--label-3)"
        return None
    # borders
    if low in BRAND_GREENS:
        return "var(--tint)"
    if light > 0.85 and low not in {"#fff", "#ffffff", "white"}:
        return "var(--separator-strong)"
    return None


def dark_spans(css: str) -> list[tuple[int, int]]:
    spans = []
    for match in re.finditer(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{", css):
        depth, index = 1, match.end()
        while depth and index < len(css):
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        spans.append((match.start(), index))
    return spans


def rewrite(css: str) -> tuple[str, int]:
    spans = dark_spans(css)
    changes = 0
    out = []
    last = 0
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = block.group(1), block.group(2)
        start = block.start(2)
        if "dark" in selector or any(a <= start < b for a, b in spans):
            continue

        def swap(decl: re.Match[str]) -> str:
            nonlocal changes
            prop, sep, value = decl.groups()
            if "var(--" in value and not HEX.search(value):
                return decl.group(0)

            def one(hex_match: re.Match[str]) -> str:
                nonlocal changes
                replacement = token(prop, hex_match.group(0))
                if replacement:
                    changes += 1
                    return replacement
                return hex_match.group(0)

            return prop + sep + HEX.sub(one, value)

        new_body = DECL.sub(swap, body)
        out.append(css[last:start])
        out.append(new_body)
        last = block.end(2)
    out.append(css[last:])
    return "".join(out), changes


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
        updated, count = rewrite(original)
        if count:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(updated)
        print(f"{count:4d}  {path}")
