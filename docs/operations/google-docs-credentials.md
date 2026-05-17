# Google Docs service-account credentials (KB-RAG Sub-4 / GH #117)

This runbook covers the **manual operator setup** required for the
`apps.kb.services.gdocs_client.GoogleDocsClient` to read Google Docs
into the KB pipeline. The code path is ready and tested with mocks; the
GCP side has to happen once per environment by hand.

> **Why service account (not OAuth)?** The platform is the consumer;
> there's no interactive user to sign in. One service account per
> environment, one JSON key on disk, no token-refresh edge cases.

---

## Setup (one-time, for new operators)

1. **Open the GCP Console** — <https://console.cloud.google.com/>. Use
   an existing project or create a new one (e.g. `ai-bot-platform-prod`).
2. **Enable two APIs** for the project:
    * Google Docs API
    * Google Drive API

   Both are required: the Docs API alone can't fetch a doc that lives on
   a user's Drive without the Drive scope too.
3. **Create the service account.** IAM & Admin → Service Accounts →
   Create Service Account:
    * Name: `gdocs-kb-reader`
    * No project-level role needed (the SA's permissions come from
      per-document sharing, not IAM).
4. **Generate a key.** Open the SA → Keys tab → Add Key → Create new
   key → JSON. The browser downloads `gdocs-kb-reader-<id>.json`.
5. **Place the key file**:
    * **Local dev**: save as
      `infra/secrets/gdocs-sa.json` (already in `.gitignore`).
    * **Staging / production**: upload to the deploy platform's secret
      store (Cloud Run secret volume, Fly.io secret mount, k8s
      `Secret`, etc.) and mount at the same path inside the container.
6. **Share each source Google Doc with the service account.** Open the
   Doc → Share → paste the SA email (looks like
   `gdocs-kb-reader@<project>.iam.gserviceaccount.com`) → role
   **Viewer** → uncheck "Notify people" → Send.
   This step is **not optional** — Drive does not grant access through
   project membership. A doc that hasn't been shared with the SA email
   returns `403` no matter what IAM roles the SA has.
7. **Set the env var** in `.env` (or your deploy platform's
   configuration):

    ```dotenv
    GOOGLE_DOCS_SERVICE_ACCOUNT_FILE=infra/secrets/gdocs-sa.json
    ```

   The default in `config/settings/base.py` is already this path, so
   if you save the file there you can skip this step.

---

## Rotation (every 90 days or after suspected compromise)

1. Generate a new JSON key in the GCP Console (same SA).
2. Replace the file at the configured path (`infra/secrets/gdocs-sa.json`
   or your platform's secret mount).
3. Restart Celery workers + Django app servers so they pick up the new
   credentials (lazy auth caches the service handle per process).
4. **Revoke the old key** in the GCP Console → SA → Keys → ⋮ → Delete.
   Do this **after** the new key is live so a partial rollout doesn't
   strand the workers.

---

## Adding a new doc to the whitelist

There's no whitelist in code. Just share the new Doc with the SA email
as Viewer (step 6 of Setup). The Sub-5 seed command picks up the
new `doc_id` from its configuration and fetches it on the next run.

---

## Troubleshooting

| Symptom                                                  | Most likely cause                                                              | Fix                                                                                              |
| -------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `FileNotFoundError: credentials file not found`          | `GOOGLE_DOCS_SERVICE_ACCOUNT_FILE` points nowhere, or the secret didn't mount  | Confirm the env var value; in containers, check the secret-mount path and worker restart        |
| `GoogleDocsAccessError: Access denied to Google Doc ...` | The Doc wasn't shared with the SA email (or was shared with a different SA)   | Share the Doc as Viewer with the SA email shown on the SA's IAM page                              |
| `GoogleDocsNotFoundError: Google Doc ... not found`      | Typo in `doc_id`, or the doc was deleted / moved to Trash                      | Re-copy the long opaque ID from the Doc URL: `https://docs.google.com/document/d/<DOC_ID>/edit`  |
| `google.auth.exceptions.MalformedError`                  | The JSON file is corrupted or partial (download truncated)                     | Re-download the JSON key                                                                          |
| Slow first fetch in prod                                 | The Discovery SDK fetches the API schema once per worker (~300ms)              | Expected; subsequent fetches reuse the cached service handle                                     |

---

## Sub-5 integration

The Sub-5 ticket (KB-RAG seed management command) will call
`GoogleDocsClient` from a Django management command. That ticket
will document the `doc_id` configuration mechanism (per-tenant table
vs settings constant — TBD at design time). Operators wiring up a new
tenant will:

1. Follow this runbook to set up the SA + share the Docs.
2. Run the Sub-5 seed command, passing the tenant slug.

Until Sub-5 lands, the client is callable directly from a Django shell
for manual testing — useful for verifying that an operator's sharing
setup actually works:

```python
from django.conf import settings
from apps.kb.services.gdocs_client import GoogleDocsClient

client = GoogleDocsClient(credentials_path=settings.GOOGLE_DOCS_SERVICE_ACCOUNT_FILE)
doc = client.fetch_document("<DOC_ID_FROM_URL>")
print(doc.title, len(doc.paragraphs))
```

---

## Security notes

* The credentials file is gitignored via `infra/secrets/*.json`. CI's
  `detect-secrets` pre-commit hook is a second line of defence: if a
  key ever lands in the index, the hook fails on the `PrivateKeyDetector`
  rule before the commit completes.
* The client logs the credentials **path** exactly once at INFO level on
  first auth (so an operator debugging "wrong file mounted" has a
  breadcrumb). It never logs the file **contents** and never includes
  the path in errors that surface to end users.
* Rotation is operator-driven on a 90-day cadence. There is no
  automatic rotation — service-account keys don't expire on the GCP
  side and need a human decision.
