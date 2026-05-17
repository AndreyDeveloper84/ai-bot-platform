# Google Docs source access (public link)

This runbook covers the **manual operator setup** required for the
`apps.kb.services.gdocs_client.GoogleDocsClient` to read Google Docs
into the KB pipeline. No GCP project, no service account, no JSON key
file — just a per-doc share toggle that any operator can flip.

> **Why public link (not a service account)?** The previous design
> (PR #121 / Sub-4) used a service account, but operator GCP orgs
> increasingly enforce `iam.disableServiceAccountKeyCreation` ("Secure
> by Default") which blocks JSON key generation. The four source
> documents are owned by the salon operator and are already
> link-shareable; the public Markdown export endpoint
> (`/document/d/<id>/export?format=md`) works anonymously. See GH #128
> for the pivot rationale.

---

## One-time setup per source doc

For each Google Doc the KB pipeline ingests:

1. **Open the doc in Google Docs.**
2. Click **Share** (top right) → in "General access" change
   "Restricted" to **"Anyone with the link"**.
3. Confirm the role is **"Viewer"** (not Editor or Commenter).
4. Click **Done**.
5. **Copy the doc ID** from the URL — it's the long opaque string
   between `/d/` and `/edit`:

    ```
    https://docs.google.com/document/d/1w8lG5I5FUQR8iFWicu4OIyr7joRqpyyzElq1kvCUqwA/edit
                                       └──────────────── doc_id ─────────────────┘
    ```

   That doc ID goes into the Sub-5 seed command's input
   (env var or management-command arg — Sub-5 ticket pins the exact
   mechanism).

There is no whitelist anywhere in code. The bot only reads the doc IDs
it is told to read; access control happens entirely on the Google
Docs side via the share toggle.

---

## Verification

After flipping the share toggle, verify access from a Django shell:

```bash
./.venv/Scripts/python.exe manage.py shell
```

```python
from apps.kb.services.gdocs_client import GoogleDocsClient

doc = GoogleDocsClient().fetch_document("<DOC_ID_FROM_URL>")
print(f"Title: {doc.title}")
print(f"Paragraphs: {len(doc.paragraphs)}")
print(f"First heading: {next((p.text for p in doc.paragraphs if p.heading_level), None)}")
```

A successful run prints the doc's first H1 heading as the title and a
non-zero paragraph count. If you get a `GoogleDocsAccessError`, the
share toggle didn't take — see Troubleshooting below.

---

## Troubleshooting

| Symptom                                                                 | Most likely cause                                                                            | Fix                                                                                                          |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `GoogleDocsNotFoundError: Doc not found: <doc_id>`                      | Wrong `doc_id` (typo) or the doc was deleted / moved to Trash                                | Re-copy the long opaque ID from the URL: `https://docs.google.com/document/d/<DOC_ID>/edit`                  |
| `GoogleDocsAccessError: Doc not link-shareable: <doc_id>`               | Most common — the owner forgot to flip "Restricted" → "Anyone with the link"                 | Re-open the doc → Share → set "General access" to "Anyone with the link", role "Viewer"                      |
| `GoogleDocsAccessError: HTTP <5xx>`                                     | Google's export pipeline transient error or rate-limit on this IP                            | Retry once. If persistent, check Google Workspace Status; the client does NOT retry — re-run the seed command |
| `paragraphs` is empty / very small but no error                         | The doc itself is empty, or the export got rate-limited and returned a stub                  | Open the doc in a browser to confirm content; if non-empty, retry after a few minutes                        |
| Title shows up as `"Untitled"` or wrong heading                         | Doc has no `# H1` at all (export uses the first heading at any level)                        | Add an H1 at the top of the doc, or accept the fallback (logged at WARN with `doc_id` for audit)             |

---

## Security notes

* **No credentials on disk.** The client takes no auth and stores no
  state. The previous SA-based design's rotation runbook does not
  apply.
* **Doc IDs are 24+ char opaque strings.** Brute-force discovery is
  infeasible (~10²⁰ keyspace, no oracle, Google rate-limits anonymous
  exports).
* **Doc IDs live in env / management-command args** — never in any URL
  the bot serves to end users. There is no public endpoint that
  echoes or accepts a doc_id.
* **Defence-in-depth fallback.** If access ever needs to be tightened
  to a specific identity (e.g. for audit logging, or because Google
  starts rate-limiting anonymous exports), the pivot is single-file:
  swap `httpx.Client` for `googleapiclient.discovery.build` inside
  `apps/kb/services/gdocs_client.py` and re-introduce a credentials
  load in `__init__`. The public surface (`GoogleDoc`,
  `GoogleDocParagraph`, `fetch_document`, the exception classes) does
  not change, so consumers (Sub-5 seed command, anything downstream)
  keep working.

---

## Sub-5 integration

The Sub-5 ticket (KB-RAG seed management command) calls
`GoogleDocsClient` from a Django management command. That ticket
pins the `doc_id` configuration mechanism (per-tenant table vs
settings constant). Operators wiring up a new tenant:

1. Follow the per-doc setup above for each source doc.
2. Verify each doc with the shell snippet above.
3. Run the Sub-5 seed command, passing the tenant slug.

Until Sub-5 lands, the client is callable directly from a Django shell
for manual testing — useful for verifying that an operator's sharing
setup actually works.
