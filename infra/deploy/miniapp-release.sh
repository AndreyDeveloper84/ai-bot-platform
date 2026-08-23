#!/usr/bin/env bash
#
# Build the Mini App into a fresh release directory and flip a symlink at it.
# DRF-1257.
#
# WHY NOT `npm run build` IN PLACE
# --------------------------------
# `vite build` empties its output directory before it writes anything. nginx
# serves `apps/miniapp/dist` directly as `root`, so for the whole build there is
# nothing on disk to serve. On 2026-08-21 the monitor caught exactly that: 403
# on BOTH miniapp-dev.gobeauty.site and proapp.gobeauty.site, which share this
# one directory. Eight seconds of build, eight seconds of outage on two domains,
# and a real alarm at the end of it.
#
# A build that fails midway is worse still: in-place, it leaves no site at all
# and no way back.
#
# This script never writes into the directory nginx is reading. It builds into
# `releases/<timestamp>-<sha>/`, verifies the result, and only then repoints
# `dist` -- a symlink -- at the new release with an atomic rename(2). Readers
# see the old release or the new one, never an empty directory. Downtime is not
# reduced; there is none. The previous releases stay on disk, so the mandatory
# pre-build backup and the rollback path are the same artefact.
#
# USAGE (on the pilot host)
#   ./infra/deploy/miniapp-release.sh                 # build + publish
#   ./infra/deploy/miniapp-release.sh --dry-run       # build + verify, no swap
#   ./infra/deploy/miniapp-release.sh --rollback      # repoint at previous release
#
# See docs/runbooks/miniapp-deploy.md for the one-time host preparation.

set -euo pipefail

DEPLOY_ROOT="${MINIAPP_DEPLOY_ROOT:-/home/taximeter/ai-bot-platform-dev}"
APP_DIR="${DEPLOY_ROOT}/apps/miniapp"
RELEASES_DIR="${APP_DIR}/releases"
LIVE_LINK="${APP_DIR}/dist"
VERIFY_URL="${MINIAPP_VERIFY_URL:-https://miniapp-dev.gobeauty.site}"
KEEP_RELEASES="${MINIAPP_KEEP_RELEASES:-5}"

MODE="publish"
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --rollback) MODE="rollback" ;;
    "") ;;
    *) echo "usage: $0 [--dry-run|--rollback]" >&2; exit 2 ;;
esac

log() { printf '[miniapp-release] %s\n' "$*"; }
die() { printf '[miniapp-release] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "$APP_DIR" ]] || die "no such directory: ${APP_DIR}"

# ---------------------------------------------------------------- rollback ---
# `dist` is a symlink, so going back is repointing it. No rebuild, no window.
if [[ "$MODE" == "rollback" ]]; then
    [[ -L "$LIVE_LINK" ]] || die "${LIVE_LINK} is not a symlink -- nothing to roll back to"
    current="$(readlink -f "$LIVE_LINK")"
    previous="$(find "$RELEASES_DIR" -maxdepth 1 -mindepth 1 -type d \
        | sort | grep -v "^${current}$" | tail -1 || true)"
    [[ -n "$previous" ]] || die "no previous release in ${RELEASES_DIR}"
    ln -sfn "$previous" "${LIVE_LINK}.swap"
    mv -Tf "${LIVE_LINK}.swap" "$LIVE_LINK"
    log "rolled back: $(basename "$current") -> $(basename "$previous")"
    exit 0
fi

# ------------------------------------------------------------ node runtime ---
# The system node on this host is 16; Vite 5 refuses it and vitest 4's
# dependencies need >=20.19. The version is declared once, in
# apps/miniapp/.nvmrc, so CI and this host cannot silently disagree.
# If it is not installed we stop with an instruction rather than fall through
# to the system node and fail deep inside a build with an unrelated message.
if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
    want="$(tr -d '[:space:]' < "${APP_DIR}/.nvmrc")"
    nvm use "$want" >/dev/null 2>&1 \
        || die "Node ${want} (apps/miniapp/.nvmrc) is not installed. Run: nvm install ${want}"
fi

node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
[[ "$node_major" -ge 20 ]] \
    || die "node ${node_major}.x is too old for this build (need >= 20); nvm was not usable"
log "node $(node --version), npm $(npm --version)"

# -------------------------------------------------------------- build step ---
cd "$APP_DIR"

# `npm ci` and not `npm install`: on 2026-08-23 the host had react-router-dom
# 6.30.4 installed while package-lock.json pins 6.30.3, so the bundle people
# were served linked a dependency version this repository never pinned. `ci`
# wipes node_modules and reproduces the lockfile exactly.
log "npm ci (reproducing package-lock.json exactly)"
npm ci

sha="$(git -C "$DEPLOY_ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${RELEASES_DIR}/${stamp}-${sha}"
mkdir -p "$RELEASES_DIR"

log "building into ${target} (nginx keeps serving the current release throughout)"
# --outDir is inside apps/miniapp, so Vite's "outDir outside project root"
# refusal does not apply. --emptyOutDir is safe here and only ever empties
# this brand-new directory, never the live one.
npx tsc --noEmit
npx vite build --outDir "$target" --emptyOutDir

# ------------------------------------------------------------ verification ---
# Verify before swapping, so a broken build never becomes the live site.
[[ -f "${target}/index.html" ]] || die "build produced no index.html -- refusing to publish"
new_asset="$(grep -oE 'index-[A-Za-z0-9_-]+\.js' "${target}/index.html" | head -1 || true)"
[[ -n "$new_asset" ]] || die "build produced no hashed JS asset -- refusing to publish"
[[ -f "${target}/assets/${new_asset}" ]] || die "${new_asset} referenced but not emitted"
log "build verified: ${new_asset}"

if [[ "$MODE" == "dry-run" ]]; then
    log "--dry-run: built and verified, live symlink untouched."
    log "artefact: ${target}"
    exit 0
fi

# -------------------------------------------------------------- the swap -----
if [[ -L "$LIVE_LINK" ]]; then
    # Steady state. `ln` writes a new symlink beside the live one and `mv -T`
    # renames it over the top: a single atomic rename(2). There is no instant
    # at which `dist` is missing or half-written.
    ln -sfn "$target" "${LIVE_LINK}.swap"
    mv -Tf "${LIVE_LINK}.swap" "$LIVE_LINK"
elif [[ -e "$LIVE_LINK" ]]; then
    # First run only: `dist` is still a real directory. It cannot be renamed
    # over by a symlink, so it is preserved as a release (this is the mandatory
    # pre-build backup) and replaced. The gap here is two syscalls, not a build.
    adopted="${RELEASES_DIR}/${stamp}-preexisting"
    log "first run: preserving the existing dist/ as $(basename "$adopted")"
    mv -T "$LIVE_LINK" "$adopted"
    ln -sfn "$target" "$LIVE_LINK"
else
    ln -sfn "$target" "$LIVE_LINK"
fi
log "published: dist -> $(basename "$target")"

# nginx resolves `root` per request and symlinks are followed by default
# (disable_symlinks is off, confirmed on this host), so no reload is required.
# The two vhosts that share this directory -- miniapp-dev.gobeauty.site and
# proapp.gobeauty.site -- both flip at the same instant.

# ------------------------------------------------- proof, from the outside ---
# A file on disk is not the claim being made. The claim is that a person's
# browser receives this code, so the check is an HTTP request from outside.
if command -v curl >/dev/null 2>&1; then
    served="$(curl -fsS "${VERIFY_URL}/" | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1 || true)"
    if [[ "$served" == "$new_asset" ]]; then
        log "verified over HTTPS: ${VERIFY_URL} serves ${served}"
    else
        die "${VERIFY_URL} serves '${served}', expected '${new_asset}' -- publish did not take"
    fi
fi

# The strongest available proof: every module in the bundle a browser just
# downloaded matches this checkout, compared as source text.
if command -v python3 >/dev/null 2>&1 && [[ -f "${DEPLOY_ROOT}/tools/ci/miniapp_bundle_drift.py" ]]; then
    ( cd "$DEPLOY_ROOT" \
      && python3 tools/ci/miniapp_bundle_drift.py --url "$VERIFY_URL" --dist "$target" ) \
      || die "drift guard still red after publish -- investigate before walking away"
fi

# ------------------------------------------------------------- retention -----
# Keep the last N releases: each one is a backup and a one-command rollback.
mapfile -t old < <(find "$RELEASES_DIR" -maxdepth 1 -mindepth 1 -type d | sort -r | tail -n +$((KEEP_RELEASES + 1)))
for dir in "${old[@]:-}"; do
    [[ -n "$dir" && "$dir" != "$(readlink -f "$LIVE_LINK")" ]] || continue
    log "pruning old release $(basename "$dir")"
    rm -rf "$dir"
done

log "done."
