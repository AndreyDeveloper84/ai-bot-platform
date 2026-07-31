"""Shared, dependency-neutral helpers for the bookings app.

Modules here must not import :mod:`apps.bookings.tasks`,
:mod:`apps.bookings.escalation`, or :mod:`apps.bookings.followups` —
those beat modules import *from* here. Keeping the dependency
direction one-way (beats → services) is what keeps the Celery task
modules free of import cycles.
"""
