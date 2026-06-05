"""Master ↔ Admin internal chat (Master-Admin Internal Chat handoff 2026-05-19).

The platform-internal channel masters use to write to their salon's
admin team about earnings disputes, leave requests, review concerns,
substitution, offboarding, and general admin-side topics. This is
**not** a customer-facing channel and **not** AI-mediated — per the
handoff §2.2 «AI is silent here. Internal admin-master communication
= professional, direct.»

PR 6 lands the data layer + basic CRUD endpoints — the production
blocker for the five downstream tracks (earnings disputes, leave
requests, review concerns, substitution, offboarding). Mini App
frontends, SLA-breach Celery beat, PII scan pipeline, founder-response
flow, and AI topic-classification are deliberate scope cuts and land
in follow-up PRs (see handoff §16 and the PR description for the
ordered cut list).
"""
