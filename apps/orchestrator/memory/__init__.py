"""Short-term + (Sprint 3) long-term memory primitives for the orchestrator.

Sprint 2 ships only `short_term` — a Redis LIST per Conversation. Long-term
Postgres-backed summary lands in Sprint 3 alongside skill dispatch (no
caller for long-term recall until then; see plan locked decision #2).
"""
