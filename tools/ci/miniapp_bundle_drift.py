#!/usr/bin/env python3
"""Fail loudly when the Mini App a human actually receives is not the Mini App in git.

DRF-1257. ``apps/miniapp/`` is static on disk, not a container. The bot deploy
pipeline rebuilds four Python services and never touches it, so a merged
front-end change can sit in ``dev`` for weeks while every browser keeps serving
the previous bundle. On 2026-08-20 that gap was measured at twelve days: the
built ``dist`` on the pilot was dated 8 August while the owner was reviewing
screenshots of it against a backend rebuilt that morning.

Why this checks content and not timestamps
------------------------------------------
A modification time proves a build ran, not that it built the current tree:
rebuilding an old checkout refreshes every mtime and looks perfectly fresh.
This guard instead reads what the *served bundle is made of*.

``apps/miniapp/vite.config.ts`` sets ``build.sourcemap = true``, so every deploy
publishes ``assets/index-<hash>.js.map`` next to the bundle, and that map
carries ``sourcesContent`` -- the verbatim text of all application modules as
they existed at build time. Downloading it over HTTPS and diffing it against the
working tree compares *the code a person's browser executes* against the code in
this commit. Nothing on the deploy host is trusted and no filesystem is read
except this repository's own ``src/``.

Line endings are noise, not drift
---------------------------------
Sources checked out on Windows carry CRLF; a build on the Linux pilot embeds LF.
Comparing raw bytes reports every module as different and means nothing -- the
trap the first pass at this ticket fell into. Every comparison here is on
LF-normalized, BOM-stripped text, so the only failures reported are real ones.

What this does and does not cover
---------------------------------
Covered exactly: every module reachable from the entrypoint, which is all
application logic. Covered when ``--dist`` is passed: the extracted stylesheet,
compared byte-for-byte against a fresh local build (CSS output is stable across
toolchains -- verified: a Node 24 build and the pilot's Node 20 build emit an
identical ``index-foET4UYR.css``).

NOT covered: modules tree-shaken out of the bundle because nothing imports them.
That is not a gap worth closing -- adding a module requires editing an importer,
every importer *is* in the bundle, so the new edge surfaces as a diff in the
file that introduced it.

The JS bundle's own content hash is deliberately never compared: it shifts with
the minifier and the Node version even when the sources are identical, which
would make this guard cry wolf. Sources are the invariant; bytes are not.

Usage::

    python tools/ci/miniapp_bundle_drift.py
    python tools/ci/miniapp_bundle_drift.py --url https://... --dist apps/miniapp/dist

Exit codes:
    0  the served bundle matches this tree
    1  drift -- the served bundle was built from different sources
    2  cannot tell (host unreachable, no sourcemap published). Also a failure,
       because "we stopped being able to check" must never read as "all clear".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://miniapp-dev.gobeauty.site"
DEFAULT_SRC = Path("apps/miniapp/src")
TIMEOUT = 30

# Vite rewrites module paths relative to the emitted asset, so an application
# module arrives as some number of `../` followed by `src/<path>`. How many is
# not a constant: it is the depth of the output directory under
# `apps/miniapp/`, and the two builds that exist here disagree.
#
#   npx vite build                      -> dist/assets/          -> ../../src/
#   infra/deploy/miniapp-release.sh     -> releases/<id>/assets/ -> ../../../src/
#
# Pinning the in-place depth (`../../src/`, the original of this constant) made
# the guard blind to every bundle the release script publishes -- that is, to
# every bundle a person has actually been served since 2026-08-23, the day the
# script landed. Both runs after it went red reporting "could not run", never
# naming the two merged commits that had in fact not been published. Worse, the
# script's own closing proof step runs this file, so a correct publish could
# never finish cleanly either.
#
# Depth is therefore matched, not assumed. `node_modules` sits at the same
# depth and is deliberately not matched: it is pinned by package-lock.json.
APP_MODULE_PREFIX = re.compile(r"^(?:\.\./)*src/")

BOM = "﻿"

ASSET_JS = re.compile(r"/assets/(index-[A-Za-z0-9_-]+\.js)")
ASSET_CSS = re.compile(r"/assets/(index-[A-Za-z0-9_-]+\.css)")


class CannotCheck(Exception):
    """The comparison could not be performed at all. Never silently tolerated."""


def fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:  # noqa: S310
            if resp.status != 200:
                raise CannotCheck(f"{url} returned HTTP {resp.status}")
            data: bytes = resp.read()
            return data
    except urllib.error.HTTPError as exc:
        raise CannotCheck(f"{url} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CannotCheck(f"{url} unreachable: {exc}") from exc


def normalize(text: str) -> str:
    """Strip the differences that are tooling artefacts rather than code changes."""
    return text.replace(BOM, "").replace("\r\n", "\n").replace("\r", "\n")


def served_modules(base_url: str) -> tuple[dict[str, str], str, str]:
    """Return ({relative src path: source text}, js asset name, css asset name)."""
    index_html = fetch(f"{base_url}/").decode("utf-8", errors="replace")

    js_match = ASSET_JS.search(index_html)
    if js_match is None:
        raise CannotCheck(
            f"no /assets/index-*.js referenced by {base_url}/ -- "
            "the served page is not a Vite build of this app"
        )
    js_name = js_match.group(1)
    css_match = ASSET_CSS.search(index_html)
    css_name = css_match.group(1) if css_match else ""

    raw_map = fetch(f"{base_url}/assets/{js_name}.map")
    try:
        source_map = json.loads(raw_map)
    except json.JSONDecodeError as exc:
        raise CannotCheck(f"{js_name}.map is not valid JSON: {exc}") from exc

    contents = source_map.get("sourcesContent")
    if not contents:
        raise CannotCheck(
            f"{js_name}.map carries no sourcesContent -- `build.sourcemap` must stay "
            "enabled in apps/miniapp/vite.config.ts or this guard goes blind"
        )

    modules: dict[str, str] = {}
    for raw_path, content in zip(source_map.get("sources", []), contents, strict=False):
        if content is None:
            continue
        path = str(raw_path).replace("\\", "/")
        match = APP_MODULE_PREFIX.match(path)
        if match is None:
            continue  # node_modules -- pinned by package-lock.json, not by this guard
        modules[path[match.end() :]] = content

    if not modules:
        seen = sorted({str(p).rsplit("/", 1)[0] + "/" for p in source_map.get("sources", [])[:20]})
        raise CannotCheck(
            f"{js_name}.map carries no `<../>*src/` module paths; the directories it does "
            f"carry are {seen} -- this is not a build of apps/miniapp"
        )
    return modules, js_name, css_name


def compare_sources(modules: dict[str, str], src_root: Path) -> tuple[list[str], list[str]]:
    """Return (modules whose text drifted, modules the served build has but this tree lacks)."""
    drifted: list[str] = []
    vanished: list[str] = []
    for rel, served_text in sorted(modules.items()):
        local = src_root / rel
        if not local.is_file():
            vanished.append(rel)
            continue
        if normalize(local.read_text(encoding="utf-8")) != normalize(served_text):
            drifted.append(rel)
    return drifted, vanished


def compare_css(base_url: str, css_name: str, dist: Path) -> str | None:
    """Compare the served stylesheet against a fresh build. Returns a reason on mismatch."""
    local_css = sorted((dist / "assets").glob("index-*.css"))
    if not local_css:
        raise CannotCheck(f"--dist {dist} has no assets/index-*.css -- was `vite build` run?")
    if len(local_css) > 1:
        raise CannotCheck(f"--dist {dist} holds several index-*.css -- cannot pick one")
    if not css_name:
        return "the served page links no stylesheet, but the local build emits one"
    if fetch(f"{base_url}/assets/{css_name}") != local_css[0].read_bytes():
        return f"served {css_name} differs from freshly built {local_css[0].name}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini App served-bundle drift guard (DRF-1257)")
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"origin to check (default {DEFAULT_URL})"
    )
    parser.add_argument(
        "--src", type=Path, default=DEFAULT_SRC, help="Mini App sources in this tree"
    )
    parser.add_argument("--dist", type=Path, help="freshly built dist/, enables the CSS comparison")
    args = parser.parse_args()

    base_url = str(args.url).rstrip("/")
    if not args.src.is_dir():
        print(f"::error::{args.src} is not a directory -- run this from the repository root")
        return 2

    try:
        modules, js_name, css_name = served_modules(base_url)
        drifted, vanished = compare_sources(modules, args.src)
        css_problem = compare_css(base_url, css_name, args.dist) if args.dist else None
    except CannotCheck as exc:
        print(f"::error::Mini App drift check could not run: {exc}")
        print(
            "This is a failure, not a pass: the guard exists precisely so that "
            "'we can no longer tell' is never mistaken for 'nothing has drifted'."
        )
        return 2

    print(f"Checked {base_url} -- bundle {js_name}, {len(modules)} application modules.")

    if not drifted and not vanished and css_problem is None:
        print(f"OK: every module served by {base_url} matches this tree.")
        return 0

    print("")
    print(f"::error::Mini App DRIFT -- {base_url} is not serving the code in this commit.")
    for rel in drifted:
        print(f"::error file=apps/miniapp/src/{rel}::served bundle was built from an older {rel}")
    for rel in vanished:
        print(f"::error::served bundle contains src/{rel}, which no longer exists in this tree")
    if css_problem is not None:
        print(f"::error::stylesheet drift -- {css_problem}")

    css_state = "drifted" if css_problem else "ok"
    print("")
    print(f"{len(drifted)} module(s) drifted, {len(vanished)} removed, css={css_state}.")
    print("People are looking at an older interface than this branch describes.")
    print("Rebuild and publish: infra/deploy/miniapp-release.sh (docs/runbooks/miniapp-deploy.md).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
