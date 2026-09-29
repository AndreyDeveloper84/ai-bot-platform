# Runbook — publishing the Mini App

**Tickets:** DRF-1257 (drift guard), DRF-1538 (publication moved into the
pipeline). **Applies to:** `apps/miniapp/` on the pilot host
(`taximeter@176.119.159.141`, `/home/taximeter/ai-bot-platform-dev`).

> **Dead host — do not work on `194.87.99.126`.** SSH to it still succeeds and
> every command will report success, but the box serves nobody: `miniapp-dev`,
> `proapp`, `dev` and `api-dev` `.gobeauty.site` all resolve to
> `176.119.159.141`, and the `.126` vhost only proxies there. A change made on
> `.126` never reaches a person. The pilot is `176.119.159.141`,
> `/home/taximeter/ai-bot-platform-dev`, Compose project `ayla-bot-staging`,
> port 8014, env `.env.staging`, files `docker-compose.yml` +
> `docker-compose.staging.yml` + `docker-compose.staging.local.yml`.

---

## 1. The short version — do nothing

**Publishing the Mini App is not a manual step any more.** Merge to `dev` and
the pipeline publishes. There is no command for you to run on the host, and
running one is a way to make things worse rather than better.

Concretely, do **not**:

- **do not** `ssh` to the pilot to build the Mini App — there is no Node on
  the box (checked 2026-09-06: no nvm under the deploy user), so a build there
  either fails or silently uses a Node that does not match CI;
- **do not** run `infra/deploy/miniapp-release.sh`. It is the old host-side
  path from DRF-1257 and it builds *on the host*, which is exactly the
  prerequisite that no longer holds. It is not what publishes today;
- **do not** hand-copy a `dist/` you built on your laptop. Your Node is not the
  runner's Node, and nothing downstream will tell you that it differed;
- **do not** publish "just this once, it is urgent". The whole point of the
  twelve-day incident in §6 was that nobody could tell which of those things
  had last happened.

---

## 2. What actually publishes, and when

`.github/workflows/deploy-dev.yml` does it, automatically:

| Step in `deploy-dev` | What it does |
| --- | --- |
| trigger | `workflow_run` — fires when the `ci` workflow **completes successfully on `dev`**. No human presses anything. |
| `Checkout dev (для сборки Mini App)` | Takes the sources from `dev`, not from the host's checkout. |
| `Node 20` + `Build Mini App on the runner` | `npm ci --no-audit --no-fund` then `npm run build`, on the runner. Asserts `dist/index.html` is non-empty and `dist/assets/` is non-empty before going further — vite writes `index.html` first and can still fail on the assets. |
| `Ship Mini App to the box and swap` | `tar` over ssh into `apps/miniapp/dist.new`, verifies it there, then two `mv`: the live `dist` becomes `dist.prev` and `dist.new` becomes `dist`. |
| `Smoke — Mini App answers over https` | Reads the bundle name out of the served `index.html` and downloads it. Informational (`continue-on-error`) on purpose: nginx failures and build failures are different failures, and conflating them helps nobody. |

Two properties worth knowing, because they used to be false:

- **No outage window.** `vite build` empties its output directory *before*
  writing, and on 2026-08-21 the monitor caught the consequence: 403 on both
  `miniapp-dev.gobeauty.site` and `proapp.gobeauty.site`, which share this one
  directory, for the eight seconds of an in-place build. The pipeline never
  touches the live `dist` until the new build is on disk in full; the swap is
  two renames.
- **Built where CI built it.** The runner's environment is fixed and is the one
  the PR was checked in. Building on the host meant a second toolchain that
  could quietly drift — on 2026-08-23 the host had `react-router-dom` 6.30.4
  installed while `package-lock.json` pinned 6.30.3, so the bundle people were
  served linked a version this repository never pinned.

---

## 3. Where to look at the result

1. **The deploy run.** `gh run list -R AndreyDeveloper84/ai-bot-platform
   --workflow=deploy-dev.yml`. The publish is inside the same run as the Python
   deploy; there is no separate Mini App workflow to look for.
2. **The swap step's own output.** `Ship Mini App to the box and swap` ends with
   `stat -c '%y %n' dist/index.html`, so the run log states the timestamp that
   is now live.
3. **From outside, over HTTPS** — see §4. This is the only one of the three that
   proves a *person* is receiving the change.

If the run is green and the site is stale, the failure is between nginx and the
directory, not in the build. Do not respond by building something by hand.

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

For the complete answer — *every* module, not one string:

```bash
python3 tools/ci/miniapp_bundle_drift.py --url https://miniapp-dev.gobeauty.site
```

`apps/miniapp/vite.config.ts` sets `build.sourcemap = true`, so each deploy
publishes `assets/index-<hash>.js.map`, and that map carries `sourcesContent` —
the verbatim text of the application modules as of build time. The guard
downloads it and diffs it against the checkout. It compares *the code a
browser executes*, and trusts nothing on the host.

Two traps it handles, both of which have already burned someone here:

- **Line endings.** Sources checked out on Windows carry CRLF; a Linux build
  embeds LF. Raw byte comparison reports every module as different and means
  nothing. Every comparison is LF-normalized.
- **Bundle hashes.** `index-<hash>.js` changes with the minifier and Node
  version even when the sources are identical, so the hash is never compared.
  Sources are the invariant; bytes are not.

`.github/workflows/miniapp-drift.yml` runs the same guard **after every
successful `deploy-dev`**, plus on a daily schedule at 06:17 UTC.

It used to run on every push to `dev` touching `apps/miniapp/**`, and that was
wrong in a way worth remembering (DRF-1605): publication only happens after a
green `ci`, 25-40 minutes later, so a push-triggered run always caught the
pilot mid-flight and went red. Eleven of twelve runs on 2026-09-08 were red for
that reason alone. The guard was reporting merges, not drift. Triggering it on
the deploy — and only on a deploy that actually succeeded — is what gives a red
back its meaning: the deploy said it published, and the pilot says otherwise.

---

## 5. Rollback

The previous build is left on the box as `apps/miniapp/dist.prev` by the swap
step. On the **pilot** (`176.119.159.141`), not on `.126`:

```bash
cd /home/taximeter/ai-bot-platform-dev/apps/miniapp
mv dist dist.broken && mv dist.prev dist
```

This is an emergency lever, not a workflow. It survives exactly one deploy —
the next `Ship Mini App` overwrites `dist.prev`. The durable fix is to revert
on `dev` and let the pipeline publish the revert.

---

## 6. Why this runbook exists

The Mini App is **static files on disk, not a container**. For a long time the
bot deploy rebuilt only the Python services, so merging a front-end change to
`dev` published *nothing*, and the change reached people only when a human
remembered to run a build on the host.

On 2026-08-20 that memory failed for twelve days: the built `dist` on the pilot
was dated **8 August**. Every deploy in between rebuilt Python and left the
interface untouched, and the owner was reviewing screenshots of an August UI
against a backend rebuilt that morning. Everyone believed they were looking at
the current state.

Three gaps caused it; all three are closed:

| Gap | Closed by |
| --- | --- |
| Nothing ever ran `vite build` in CI | `vite build` step in the `miniapp` job of `.github/workflows/ci.yml` |
| Nothing compared what is *served* against what is in `dev` | `.github/workflows/miniapp-drift.yml` + `tools/ci/miniapp_bundle_drift.py` |
| Publication depended on a human remembering | `Build Mini App on the runner` + `Ship Mini App to the box and swap` in `.github/workflows/deploy-dev.yml` (DRF-1538) |

The last row is what turned this runbook from a procedure into a prohibition.
Everything the old §2 ("one-time host preparation" — install Node 22 on the box,
convert `dist` into a release symlink, clean up `dist.bak-*`) and the old §3
("Publishing a change" — ssh, pull, run `miniapp-release.sh`) described is
superseded: the host no longer needs a toolchain, and nobody publishes by hand.

### 6.1 Two claims this runbook used to make that are no longer true

- *"`deploy-dev.yml` has produced no automatic run since 2026-06-10, because
  `workflow_run` triggers are honoured only from the default branch and the
  default branch is `main`."* The default branch is now **`dev`**, so
  `workflow_run` fires, and `deploy-dev` runs on every green `ci` on `dev`.
- *"Adding a Mini App build step to that workflow would attach it to something
  that neither fires nor works."* It was added (DRF-1538) and it does both.

---

## 7. Known gap

`infra/deploy/miniapp-release.sh` is still in the tree and still documents a
host-side build. It is superseded by the pipeline and cannot run on the pilot
(no Node). Removing it, and the host-Node instructions it carries, needs its
own ticket — it is code, and this runbook is not the place to delete it.
Until then, treat its presence as history, not as an instruction.
