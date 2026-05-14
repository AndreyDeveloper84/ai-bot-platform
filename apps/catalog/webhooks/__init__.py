"""mysite → platform delta-push webhook.

Sprint 10 / C3 (DRF-879). Pull-side sync (Sprint 7 / C-track) refreshes
every 15 min; this webhook fires on every mysite mutation so latency-
sensitive flows (a salon edits a price and expects the bot to know
within seconds) don't wait for the next beat.

The view in ``views.py`` is the entry; the Celery task in ``tasks.py``
does the actual upsert/delete.
"""
