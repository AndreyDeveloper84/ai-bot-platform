# Runbook — publishing the Mini App

**Ticket:** DRF-1257. **Applies to:** `apps/miniapp/` on the pilot host
(`taximeter@194.87.99.126`, `/home/taximeter/ai-bot-platform-dev`).

---

## 1. Why this runbook exists

The Mini App is **static files on disk, not a container**. The bot deploy
rebuilds four Python services and never touches `apps/miniapp/`, so merging a
front-end change to `dev` publishes *nothing*. The change reaches people only
when a human remembers to run a build on the host.

On 2026-08-20 that memory failed for twelve days: the built `dist` on the pilot
was dated **8 August**. Every deploy in between rebuilt Python and left the
interface untouched, and the owner was reviewing screenshots of an August UI
against a backend rebuilt that morning. Everyone believed they were looking at
the current state.

Two things were missing, and both are now in the repository:

| Gap | Closed by |
| --- | --- |
| Nothing ever ran `vite build` — not in CI, not in the deploy | `vite build` step in the `miniapp` job of `.github/workflows/ci.yml` |
| Nothing compared what is *served* against what is in `dev` | `.github/workflows/miniapp-drift.yml` + `tools/ci/miniapp_bundle_drift.py` |
| Building in place took both Mini App domains down | `infra/deploy/miniapp-release.sh` (build aside, flip a symlink) |

---

## 2. One-time host preparation

These steps are **not** performed by CI and have not been performed by this
ticket. They need a human on the host.

### 2.1 Install the Node version the repository declares

`apps/miniapp/.nvmrc` says **22**. The host's nvm currently has only
`v20.20.0`, and the system Node is `v16.20.2`, which Vite 5 refuses.

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install 22
```

Until this is done, `nvm use` (with no argument) inside `apps/miniapp` fails
with `N/A: version "v22" is not yet installed`, which is why the ad-hoc
instructions everyone was passing around said `nvm use 20` — they were working
around a mismatch nobody had written down. CI builds on 22; the host could only
build on 20; nothing enforced that they agree.

`infra/deploy/miniapp-release.sh` now stops with that exact instruction rather
than silently falling through to Node 16.

### 2.2 Convert `dist` from a directory into a release symlink

`vite build` **empties its output directory before writing**. nginx serves
`apps/miniapp/dist` as `root`, so an in-place build means there is nothing to
serve for the duration. On 2026-08-21 the monitor caught it: **403 on both
`miniapp-dev.gobeauty.site` and `proapp.gobeauty.site`**, which share this one
directory. Eight seconds of build, eight seconds of outage on two domains.

The first run of the release script performs the conversion itself: it preserves
the existing `dist/` as a release (this is the mandatory pre-build backup) and
replaces it with a symlink. Afterwards every publish is an atomic `rename(2)`
over the symlink, and there is no window at all.

```bash
cd /home/taximeter/ai-bot-platform-dev
./infra/deploy/miniapp-release.sh --dry-run    # build + verify, live site untouched
./infra/deploy/miniapp-release.sh              # publish
```

Nothing in nginx needs to change: `root` is resolved per request, symlinks are
followed by default (`disable_symlinks` is not set on this host), so both
vhosts flip at the same instant and no reload is required.

### 2.3 Clean up the manual backups

`dist.bak-20260820/`, `dist.bak-prev/`, `dist.bak-rating-20260821/` are
leftovers from hand-run builds. Once releases exist they are redundant — the
last five releases are kept automatically and `--rollback` repoints at the
previous one. Remove them when convenient.

---

## 3. Publishing a change

```bash
ssh taximeter@194.87.99.126
cd /home/taximeter/ai-bot-platform-dev
git checkout dev && git pull --ff-only origin dev
./infra/deploy/miniapp-release.sh
```

The script runs `npm ci` (not `npm install`) on purpose. On 2026-08-23 the host
had `react-router-dom` **6.30.4** installed while `package-lock.json` pins
**6.30.3** — the bundle people were served linked a dependency version this
repository never pinned. `ci` wipes `node_modules` and reproduces the lockfile
exactly.

Rollback is a symlink flip, no rebuild:

```bash
./infra/deploy/miniapp-release.sh --rollback
```

---

## 4. Proving the change reached a person

**Not by the file's date.** An mtime proves a build ran, not that it built the
current tree — rebuilding a two-week-old checkout refreshes every timestamp and
looks perfectly fresh. That is precisely how twelve days went unnoticed.

Prove it from outside, over HTTPS:

```bash
B=$(curl -s https://miniapp-dev.gobeauty.site/ | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1)
curl -s "https://miniapp-dev.gobeauty.site/assets/$B" | grep -c "<a string from the change>"
```

The release script already does the asset-name half of this automatically and
refuses to report success if the served page does not name the bundle it just
built.

For the complete answer — *every* module, not one string:

```bash
python3 tools/ci/miniapp_bundle_drift.py --url https://miniapp-dev.gobeauty.site
```

`apps/miniapp/vite.config.ts` sets `build.sourcemap = true`, so each deploy
publishes `assets/index-<hash>.js.map`, and that map carries `sourcesContent` —
the verbatim text of all 106 application modules as of build time. The guard
downloads it and diffs it against the checkout. It compares *the code a
browser executes*, and trusts nothing on the host.

Two traps it handles, both of which have already burned someone here:

- **Line endings.** Sources checked out on Windows carry CRLF; a Linux build
  embeds LF. Raw byte comparison reports all 106 modules as different and means
  nothing. Every comparison is LF-normalized.
- **Bundle hashes.** `index-<hash>.js` changes with the minifier and Node
  version even when the sources are identical, so the hash is never compared.
  Sources are the invariant; bytes are not.

---

## 5. Known gap — this is still a human remembering

`miniapp-drift` makes forgetting **loud**, and CI now guarantees the code
*builds*. Neither makes publication automatic: a merge to `dev` still does not
reach the pilot until someone runs the release script.

Closing that last gap needs a working deploy pipeline to hang the step on, and
**there is not one**:

- `.github/workflows/deploy-dev.yml` has produced **no automatic run since
  2026-06-10**. Commit `ab2d164` ("activate auto-deploy of the dev bot")
  switched its trigger from `push: [dev]` to `workflow_run`. GitHub honours
  `workflow_run` triggers **only from the default branch**, and the default
  branch is `main` — 885 commits behind, still carrying the old skeleton. The
  commit that activated auto-deploy is what disabled it.
- The single run since then (`workflow_dispatch`, 2026-08-08, run
  `31279529908`) **failed** at "Pull + rebuild + restart dev services". It
  targets Compose project `ai-bot-platform-dev`; the pilot actually runs
  `ayla-bot-staging` (plus a systemd `ai-bot-platform-dev-consumer`).

Adding a Mini App build step to that workflow would attach it to something that
neither fires nor works. Both are Python-deploy repairs and out of scope for
DRF-1257 — they need their own ticket and an owner's decision.
