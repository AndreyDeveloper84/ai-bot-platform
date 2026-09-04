#!/usr/bin/env python3
"""Fail when the Mini App palette drifts from its single source or from AA.

DRF-1462. On 2026-09-04 the owner cancelled §21-bis and moved every
surface -- customer, salon, master -- onto the purple palette signed on
the ``DRF-1181`` board. The audit that preceded the move
(``docs/AUDIT_SOLO_MASTER_AND_COLORS.md`` §2.1) found the palette living
in **five** places at once, with two of fourteen variables still
agreeing with any canon. The board is machine-generated, so its *pixels*
disagree with its own *labels* -- the swatch labelled ``#5B68FF`` is
painted ``#041FFA`` -- and the labels are what count.

Two failure modes follow from that, and this lint refuses both.

# First check: one source

Every colour literal must live in ``apps/miniapp/src/styles/tokens.css``.
Before this lint there were 95 elsewhere, most of them hidden as dead
fallbacks -- ``var(--c-border, #e0e0e3)`` -- naming nine variables no
stylesheet ever declared. A fallback behind an undeclared variable is
not a fallback: it *is* the colour, and it is invisible to anyone
reading ``tokens.css`` to learn what the app looks like.

A hash followed by digits is not always a colour: ``see PR #798`` and
``(/customer/wellness, #951)`` are issue numbers. Only a hash in a CSS
*value position* -- after ``:`` or ``,`` -- on a line that is otherwise
about colour is counted.

# Second check: contrast

Sage had its ratios computed and written down; purple did not. The
signed values do not all survive contact with WCAG 2.2 AA: ``#22C55E``
is 2.28:1 on white and ``#F59E0B`` is 2.15:1, and both are painted as
text in ``globals.css``. ``tokens.css`` therefore carries the signed
value where it passes and a lightness-shifted one where it does not --
and this lint recomputes every pair rather than trusting the comment
written beside it.

# Why this is a lint and not a front-end test

Same reason as ``tools/lint/miniapp_style_contract.py``: ``apps/miniapp``
runs vitest with ``css: false``, so a stylesheet imported inside a test
-- including through ``?raw`` -- comes back as the empty string and
every assertion over it passes vacuously. Measured, not assumed: under
this config a ``?raw`` import of ``tokens.css`` has length 0. The check
has to read the file as text, which is what this does.

Usage::

    python tools/lint/miniapp_token_contrast.py apps/miniapp

Exit codes: 0 clean, 1 violations, 2 bad invocation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOKENS = Path("src/styles/tokens.css")
SOURCE_DIR = Path("src")
SCANNED_SUFFIXES = (".css", ".ts", ".tsx")

# WCAG 2.2 AA for body text. One threshold on purpose: a token does not
# know what size type will be painted with it.
AA = 4.5

DECLARATION = re.compile(r"^\s*--c-([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})", re.M)
DARK_AT = "@media (prefers-color-scheme: dark)"

# A colour literal in a value position: after `:` or `,`, optionally
# inside a quote so a JSX inline style -- `{ color: "#ff8a00" }` -- counts
# too. `#798` in `PR #798` has a space before it and so does not match.
VALUE_POSITION = re.compile(r"""(?::|,)\s*["']?\s*#[0-9a-fA-F]{3,8}\b""")
ABOUT_COLOUR = re.compile(r"var\(--|background|color|border|outline|shadow|fill|stroke")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/")
COMMENT_LINE = re.compile(r"^\s*(\*|//)")

# Surfaces that actually appear in `background:` in `globals.css`.
SURFACES = ("bg", "surface-1", "surface-2")
# Roles that actually appear in `color:` there.
TEXTS = (
    "text-primary",
    "text-secondary",
    "accent",
    "accent-pressed",
    "success",
    "warning",
    "danger",
)
# Pairs where the background is a coloured token, not a surface.
ON_COLOUR = (
    ("text-primary", "accent-subtle"),
    ("accent", "accent-subtle"),
    ("accent-pressed", "accent-subtle"),
    ("text-on-accent", "accent"),
    ("text-on-accent", "accent-pressed"),
    ("text-on-accent", "success"),
    ("text-on-accent", "warning"),
    ("text-on-accent", "danger"),
    # The snackbar is an inverted surface: `Snackbar.tsx` paints its
    # background with `--c-text-primary`, its message with `--c-bg` and its
    # action with `--c-accent-subtle`. Inverted or not, the text on it still
    # has to be readable, and in both themes.
    ("bg", "text-primary"),
    ("accent-subtle", "text-primary"),
)
# Roles painted as text on a wash made of themselves. Not invented: these
# are every rule in `globals.css` where `background: color-mix(... var
# (--c-X) N%, ...)` sits beside `color: var(--c-X)` -- `.callout--danger`,
# `.m6-bubble--failed`, `.unbookable-badge`, `.m-card__chip--warning`,
# `.admin-chip--warn`, `.m-notif__banner-warning`. The share is 10 %
# everywhere; raising it is what this check refuses.
SELF_WASH = ("warning", "danger")
SELF_WASH_SHARE = 0.10

# Values signed on the DRF-1181 board that survive AA untouched and so
# must stand verbatim. The dark theme swaps the two neutral poles.
SIGNED = {
    "light": {"bg": "#f8fafc", "divider": "#e5e7eb", "text-primary": "#111827"},
    "dark": {"bg": "#111827", "text-primary": "#f8fafc"},
}


def _rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def _channel(raw: int) -> float:
    c = raw / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(value: str) -> float:
    r, g, b = _rgb(value)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a: str, b: str, part: float) -> str:
    """`color-mix(in srgb, a P%, b)` -- the same blend the browser does."""
    left, right = _rgb(a), _rgb(b)
    blended = (round(left[i] * part + right[i] * (1 - part)) for i in range(3))
    return "#" + "".join(f"{c:02x}" for c in blended)


def palettes(tokens_css: str) -> dict[str, dict[str, str]]:
    cut = tokens_css.index(DARK_AT)
    return {
        "light": dict(DECLARATION.findall(tokens_css[:cut])),
        "dark": dict(DECLARATION.findall(tokens_css[cut:])),
    }


def check_single_source(root: Path) -> list[str]:
    problems: list[str] = []
    tokens = (root / TOKENS).resolve()
    for path in sorted((root / SOURCE_DIR).rglob("*")):
        if path.suffix not in SCANNED_SUFFIXES or path.resolve() == tokens:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            code = BLOCK_COMMENT.sub("", line)
            if COMMENT_LINE.match(code):
                continue
            if VALUE_POSITION.search(code) and ABOUT_COLOUR.search(code):
                rel = path.relative_to(root).as_posix()
                where = f"{rel}:{number}"
                problems.append(f"{where}: colour literal outside tokens.css -- {line.strip()}")
    return problems


def check_contrast(themes: dict[str, dict[str, str]]) -> list[str]:
    problems: list[str] = []
    light, dark = themes["light"], themes["dark"]
    if sorted(light) != sorted(dark):
        only_light = sorted(set(light) - set(dark))
        only_dark = sorted(set(dark) - set(light))
        problems.append(
            "tokens.css: the two themes declare different roles -- "
            f"light only {only_light}, dark only {only_dark}"
        )
    for theme, palette in themes.items():
        for role, expected in SIGNED[theme].items():
            actual = palette.get(role)
            if actual != expected:
                problems.append(
                    f"tokens.css [{theme}]: --c-{role} is {actual}, but the "
                    f"DRF-1181 board signs {expected} and it passes AA as "
                    "signed"
                )
        pairs = [(t, s) for t in TEXTS for s in SURFACES] + list(ON_COLOUR)
        for text, surface in pairs:
            if text not in palette or surface not in palette:
                problems.append(f"tokens.css [{theme}]: --c-{text} / --c-{surface} missing")
                continue
            ratio = contrast(palette[text], palette[surface])
            if ratio < AA:
                problems.append(
                    f"tokens.css [{theme}]: --c-{text} on --c-{surface} is "
                    f"{ratio:.2f}:1, below AA {AA}:1"
                )
        for role in SELF_WASH:
            for surface in SURFACES:
                wash = mix(palette[role], palette[surface], SELF_WASH_SHARE)
                ratio = contrast(palette[role], wash)
                if ratio < AA:
                    problems.append(
                        f"tokens.css [{theme}]: --c-{role} on its own "
                        f"{int(SELF_WASH_SHARE * 100)} % wash over "
                        f"--c-{surface} is {ratio:.2f}:1, below AA {AA}:1"
                    )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <apps/miniapp>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    tokens = root / TOKENS
    if not tokens.is_file():
        print(f"{tokens}: not found", file=sys.stderr)
        return 2

    problems = check_contrast(palettes(tokens.read_text(encoding="utf-8")))
    problems += check_single_source(root)
    if problems:
        print("Mini App palette contract violations:\n", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThe palette has one source: apps/miniapp/src/styles/tokens.css.\n"
            "Values come from the DRF-1181 board's labels, never its pixels\n"
            "(OPEN_DECISIONS §21-ter). A signed value that fails AA moves in\n"
            "lightness only -- and this lint recomputes, so it has to be true\n"
            "in the file, not only in the comment beside it.",
            file=sys.stderr,
        )
        return 1
    print("Mini App palette contract: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
