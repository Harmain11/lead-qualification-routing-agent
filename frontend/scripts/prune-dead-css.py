"""Remove CSS selectors that can never match anything Corral renders.

A selector is dead when it requires a class name that appears nowhere in the
app's source (TS/TSX/HTML). Class names built at runtime are protected by
prefix: a template literal such as `status-${x}` keeps every `status-*` class.
@keyframes, @font-face and selectors without classes are never touched.

Usage: python3 prune-dead-css.py [--write] CSS_FILE...
Prints bytes saved per file; rewrites files only with --write.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*ROOT.glob("src/**/*.ts"), *ROOT.glob("src/**/*.tsx"), *ROOT.glob("*.html"), *ROOT.glob("public/**/*.html")]

TOKEN = re.compile(r"-?[A-Za-z_][\w-]*")
CLASS = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
KEEP_AT = ("@keyframes", "@-webkit-keyframes", "@font-face", "@page", "@property", "@import", "@charset", "@namespace")


def collect_usage() -> tuple[set[str], set[str]]:
    """Every word-like token in the source counts as a possible class name.

    Deliberately over-inclusive: parsing JSX strings exactly is fragile (an
    apostrophe in JSX text looks like a string quote), and keeping a little
    extra CSS is safe while removing a live rule is not.
    """
    tokens: set[str] = set()
    prefixes: set[str] = set()
    for path in SOURCES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        tokens.update(TOKEN.findall(text))
        # `foo-${x}` and "foo-" + x protect every class starting with "foo-".
        prefixes.update(re.findall(r"([A-Za-z_][\w-]*-)\$\{", text))
        prefixes.update(re.findall(r"[\"'`]([A-Za-z_][\w-]*-)[\"'`]\s*\+", text))
    return tokens, prefixes


def used(name: str, tokens: set[str], prefixes: set[str]) -> bool:
    return name in tokens or any(name.startswith(prefix) for prefix in prefixes)


def split_selectors(selector: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in selector:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part for part in (p.strip() for p in parts) if part]


def selector_alive(selector: str, tokens: set[str], prefixes: set[str]) -> bool:
    # Classes inside :not(...) / :where(...) negations are not required to exist.
    required = re.sub(r":(?:not|where|is|has)\((?:[^()]|\([^()]*\))*\)", "", selector)
    required = re.sub(r"\[[^\]]*\]", "", required)
    return all(used(name, tokens, prefixes) for name in CLASS.findall(required))


def find_block_end(css: str, start: int) -> int:
    """Index just after the brace that closes the block opened at css[start] == '{'."""
    depth, index = 0, start
    while index < len(css):
        char = css[index]
        if css.startswith("/*", index):
            index = css.find("*/", index + 2)
            index = len(css) if index < 0 else index + 2
            continue
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


def prune(css: str, tokens: set[str], prefixes: set[str]) -> str:
    out: list[str] = []
    index = 0
    while index < len(css):
        if css.startswith("/*", index):
            end = css.find("*/", index + 2)
            end = len(css) if end < 0 else end + 2
            out.append(css[index:end])
            index = end
            continue
        brace = css.find("{", index)
        semicolon = css.find(";", index)
        if brace < 0:
            out.append(css[index:])
            break
        if 0 <= semicolon < brace and css[index:semicolon].strip().startswith("@"):
            out.append(css[index:semicolon + 1])  # @import / @layer statements
            index = semicolon + 1
            continue
        prelude = css[index:brace]
        # Comments before a rule stay as they are; only the selector is judged.
        comment_end = prelude.rfind("*/") + 2 if "*/" in prelude else 0
        out.append(prelude[:comment_end])
        prelude = prelude[comment_end:]
        end = find_block_end(css, brace)
        head = prelude.strip()
        if head.startswith("@"):
            if head.startswith(KEEP_AT):
                out.append(css[index:end])
            else:
                inner = prune(css[brace + 1:end - 1], tokens, prefixes)
                if inner.strip():
                    out.append(f"{prelude}{{{inner}}}")
        else:
            leading = prelude[: len(prelude) - len(prelude.lstrip())]
            alive = [s for s in split_selectors(head) if selector_alive(s, tokens, prefixes)]
            if alive:
                if len(alive) == len(split_selectors(head)):
                    out.append(css[index:end])
                else:
                    out.append(f"{leading}{', '.join(alive)} {css[brace:end]}")
        index = end
    return "".join(out)


def balanced(css: str) -> bool:
    """Every comment closes and every brace pairs up once comments are removed."""
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return "/*" not in bare and "*/" not in bare and bare.count("{") == bare.count("}")


if __name__ == "__main__":
    write = "--write" in sys.argv
    files = [Path(arg) for arg in sys.argv[1:] if arg != "--write"]
    tokens, prefixes = collect_usage()
    total = 0
    for path in files:
        original = path.read_text(encoding="utf-8")
        pruned = prune(original, tokens, prefixes)
        if not balanced(pruned):
            raise SystemExit(f"Refusing to write {path}: the result would not be valid CSS.")
        saved = len(original) - len(pruned)
        total += saved
        if saved:
            print(f"{saved:8d}  {path}")
            if write:
                path.write_text(pruned, encoding="utf-8")
    print(f"{total:8d}  total bytes{' removed' if write else ' removable'}")
