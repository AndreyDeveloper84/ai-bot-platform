# Runbook: connect the five unlinked salons (DRF-1510)

> Status: **draft — rehearsed on a test database, not yet exercised on the pilot**
> Last exercised: _never (on the pilot)_
> Owner: platform on-call / main window

## Purpose

Bring the five salons that exist in the Ayla backend but have no row in the
bot's `apps_tenancy_tenant` table onto the client-facing surface. Until they
are provisioned, **171 services and all 14 manicures are invisible** — not
stale, absent: `sync_catalog_for_all_tenants` iterates `Tenant.objects.all()`
(`apps/catalog/tasks.py`), so a salon with no row is never fetched.

Measured on the boevoy contour, 05.09.2026:

| slug | name | services | manicures |
| --- | --- | ---: | ---: |
| `olhovyy-dvor` | Ольховый двор | 62 | 4 |
| `fevralskiy-svet` | Февральский свет | 43 | — |
| `sorok-okon` | Сорок окон | 28 | 7 |
| `pylca-i-lyon` | Пыльца и лён | 21 | — |
| `mednyy-kovsh` | Медный ковш | 17 | 3 |
| | **total** | **171** | **14** |

Already connected: `formula-tela` 58, `mkt-mediclinic` 24, `mkt-afrodita` 8,
`mkt-lumina` 2, `mkt-spatrium` 2 = 94. 94 + 171 = 265 = the backend total.

## Prerequisites — read this before typing anything

### 1. The slug is NOT what matches the backend. The primary key is.

`CatalogSyncService._run_locked` fetches all three mirrors with
`?tenant=str(tenant.id)`, and Ayla filters its catalog on **its own Tenant
UUID**. The bot's `Tenant.id` must BE that UUID. The slug never leaves this
database — it only names the row for `sync_catalog --tenant <slug>`.

A tenant created **without** `--id` gets a fresh `uuid4` and mirrors nothing:
three fetches, zero rows, `tenants_run=5`, `tenants_failed=0`, `created=0`,
and a single `catalog.sync.empty_fetch` warning that reads exactly like a
salon which genuinely sells nothing. **The deploy would look successful and
change nothing.**

> **Blocker:** the five Ayla Tenant UUIDs are not in this repository. Get
> them from the Ayla side (`tenants` table, or
> `GET /api/v1/internal/…` with the s2s token) **before** running Step 1.
> Substitute them for the `<uuid-…>` placeholders below. Do not invent them,
> and do not run the commands without them.

### 2. `--city` is not decoration.

`apps/marketplace/discovery.py` routes a city token in the client's query to
`tenant__city` (`_bookable_qs(city=…)`, `_known_cities()`). A blank `city`
means the salon is absent from every city-scoped answer, and «Пенза» is not
even a city the query parser will recognise on its behalf. There is no
backfill command in the repo; `--city` on `create_tenant` (DRF-1510) is now
the setter. All five are in Пенза unless the owner says otherwise — see
"Open questions" below.

### 3. Access

Shell on the target box with the platform env loaded, same as
[`max-pilot-provisioning-m0.md`](max-pilot-provisioning-m0.md) Step 2a.

## Step 1 — provision the five tenants (idempotent)

Preview first. `--dry-run` writes nothing and prints the exact row it would
create:

```bash
python manage.py create_tenant --slug olhovyy-dvor    --name "Ольховый двор"    --id <uuid-olhovyy-dvor>    --city "Пенза" --dry-run
python manage.py create_tenant --slug fevralskiy-svet --name "Февральский свет" --id <uuid-fevralskiy-svet> --city "Пенза" --dry-run
python manage.py create_tenant --slug sorok-okon      --name "Сорок окон"       --id <uuid-sorok-okon>      --city "Пенза" --dry-run
python manage.py create_tenant --slug pylca-i-lyon    --name "Пыльца и лён"     --id <uuid-pylca-i-lyon>    --city "Пенза" --dry-run
python manage.py create_tenant --slug mednyy-kovsh    --name "Медный ковш"      --id <uuid-mednyy-kovsh>    --city "Пенза" --dry-run
```

Then, the same five lines without `--dry-run`:

```bash
python manage.py create_tenant --slug olhovyy-dvor    --name "Ольховый двор"    --id <uuid-olhovyy-dvor>    --city "Пенза"
python manage.py create_tenant --slug fevralskiy-svet --name "Февральский свет" --id <uuid-fevralskiy-svet> --city "Пенза"
python manage.py create_tenant --slug sorok-okon      --name "Сорок окон"       --id <uuid-sorok-okon>      --city "Пенза"
python manage.py create_tenant --slug pylca-i-lyon    --name "Пыльца и лён"     --id <uuid-pylca-i-lyon>    --city "Пенза"
python manage.py create_tenant --slug mednyy-kovsh    --name "Медный ковш"      --id <uuid-mednyy-kovsh>    --city "Пенза"
```

Expected per line:

```
Created tenant 'olhovyy-dvor' (id=<uuid-olhovyy-dvor>, name='Ольховый двор', city='Пенза')
```

Refusals — exit 1, nothing written:

| output | meaning | action |
| --- | --- | --- |
| `Invalid --id …` | the UUID is malformed, or `--id ""` reached the command | fix the value; do not proceed |
| `--id … is already used by tenant 'X'` | that UUID belongs to another slug | you have the wrong UUID, or a duplicate row exists |
| `exists with id=…, but --id asked for …` | the row was created earlier **without** `--id` | the pk cannot be re-keyed in place — see "Recovery" |

Warning — exit 0, and on a `--dry-run` it fires **before** anything is written:

| output | meaning | action |
| --- | --- | --- |
| `no --id given: … will mirror 0 services` | you forgot `--id` | add the flag and re-run the preview. If the real run already happened, see "Recovery" |

Re-runs are safe: an existing row is a no-op, and `--city` fills a blank city
without ever overwriting a non-blank one.

## Step 2 — dry-run the catalog sync, one salon at a time

Fetches from Ayla and reports what the upsert *would* change. Writes nothing:

```bash
python manage.py sync_catalog --tenant olhovyy-dvor    --dry-run
python manage.py sync_catalog --tenant fevralskiy-svet --dry-run
python manage.py sync_catalog --tenant sorok-okon      --dry-run
python manage.py sync_catalog --tenant pylca-i-lyon    --dry-run
python manage.py sync_catalog --tenant mednyy-kovsh    --dry-run
```

Expected shape — `upstream=` must equal the measured count in the table above:

```
olhovyy-dvor: upstream=62 mirrored=0 …
```

**`upstream=0` means the `--id` is wrong.** Stop. Do not run Step 3; fix the
UUID first (see "Recovery").

## Step 3 — run the sync for real

```bash
python manage.py sync_catalog --tenant olhovyy-dvor
python manage.py sync_catalog --tenant fevralskiy-svet
python manage.py sync_catalog --tenant sorok-okon
python manage.py sync_catalog --tenant pylca-i-lyon
python manage.py sync_catalog --tenant mednyy-kovsh
```

Or, once the five are known good, let the beat pick them up — it runs every
tenant on its own cadence and needs no command. Running the five explicitly
is preferred for a supervised deploy: the blast radius is one salon per line.

## Verification

```bash
python manage.py sync_catalog --status
```

Prints one line per tenant: `tenant | last sync ok | age | mirrored`. All ten
salons must show a recent `last sync ok` (no `STALE` marker), and `mirrored`
must equal the service count in the table at the top of this runbook.

`mirrored` is only half the answer, though — it says nothing about whether
anyone can be booked. The second number is the one that decides visibility:

```bash
python manage.py shell -c "
from apps.catalog.models import CatalogService, CatalogMaster
from apps.tenancy.models import Tenant
for t in Tenant.objects.order_by('slug'):
    print(t.slug,
          CatalogService.all_tenants.filter(tenant=t, is_active=True).count(),
          CatalogMaster.all_tenants.filter(tenant=t, is_active=True,
              invite_status='accepted').count(),
          repr(t.city))
"
```

Columns: slug, active services, **bookable masters**, city. A salon with
services and a zero in the middle column will sync cleanly and stay invisible
— by design, see the section below.

### The paired positive check — do not skip it

The five already-connected salons must still carry **exactly** 58 / 24 / 8 /
2 / 2 services. The deploy widens every cross-tenant read; a regression there
would show up as a smaller number, not as an error.

### Client-facing smoke

Ask the bot «какие салоны у нас есть?» — the five new names must appear —
and «маникюр» — 14 rows across Ольховый двор, Сорок окон and Медный ковш,
with no salon named in the question (owner decision §23: search is global).

## What happens to a salon with services but no bookable masters

**Established by test, not assumed** (`apps/catalog/tests/test_five_salons_drf1510.py`,
`TestSalonWithoutBookableMasters`): its services ARE mirrored, and it is
**absent from every customer-facing reader**:

* `discover_salons()` — not in the directory (`_bookable_tenants` derives from
  `_bookable_qs`, which requires `is_active` AND `invite_status='accepted'`);
* `discover_services()` — its rows are filtered out by
  `tenant_id__in=bookable_tenant_ids`;
* `discover_masters_for_service(<one of its service ids>)` — empty, even when
  the service is addressed by id;
* `get_salon(tenant_id)` — `None`.

So the DRF-1164 rule ("no performer, no booking") is enforced one layer
*earlier* than the booking gate. Such a salon does not appear-and-refuse; it
never becomes an offer at all. The failure mode is under-offering, which is
safe, and the operator-visible symptom is "the salon synced N services and
still isn't in the directory" — that is a masters-feed problem, not a bug.

Note that `CatalogMaster.invite_status` defaults to `accepted`, so masters
arriving through sync are bookable immediately; no invite step is needed to
make a newly connected salon visible.

## Recovery — a tenant created without `--id` (or with the wrong one)

The primary key cannot be changed in place: the mirror rows hang off it, and
`create_tenant` now refuses to pretend otherwise. Options, in order of
preference:

1. **The row has no mirror rows yet** (the usual case — it never synced
   anything): delete it and re-create with the right `--id`.
   ```bash
   python manage.py shell -c "
   from apps.tenancy.models import Tenant
   print(Tenant.all_objects.filter(slug='<slug>').delete())"
   ```
   `print` on purpose: `.delete()` returns `(total, {model: count})`, and
   that tuple is the evidence the delete hit one `Tenant` row and nothing
   else. A tenant FK cascades widely in this repo — if the tuple names any
   model beyond `tenancy.Tenant`, stop and escalate rather than re-creating.
   Otherwise re-run the Step 1 line, `--dry-run` first.
2. **The row has mirror rows** — do not delete it blind. Escalate; the rows
   belong to whatever tenant id they were written under.

## Open questions for the owner

1. **The five Ayla Tenant UUIDs.** Not derivable from this repo. Without them
   the deploy is a no-op that reports success.
2. **City for each salon.** All five are assumed «Пенза». If any is
   elsewhere, its `--city` must say so, or it drops out of «… в Пензе».
3. **Does every one of the five have bookable masters upstream?** If a salon
   has services but no specialists, it will sync and stay invisible (by
   design, above). Worth knowing before the deploy so "it didn't appear" is
   not read as a failure.
4. **Should the already-connected five get their `city` backfilled** in the
   same pass? Re-running their `create_tenant` line with `--city "Пенза"`
   fills a blank city and never overwrites a set one.
5. **A wrong city cannot be corrected by this command.** The
   "never overwrite a non-blank city" policy means the first value written
   is final as far as `create_tenant` goes; changing it afterwards is a
   Django admin edit. So question 2 is worth settling **before** Step 1, not
   after.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P1 — services synced, salon invisible | platform on-call | ops channel |
| P1 — a connected salon lost services | platform on-call | ops channel; consider rolling the beat off |
