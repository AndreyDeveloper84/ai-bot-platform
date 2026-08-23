"""Proactive nutrition layer — two beat tasks, not a campaign engine (DRF-1285).

Ships exactly two bot-initiated messages and nothing else:

* ``nutrition_proactive.send_daily_reports`` — the nutrition day summary at
  the hour the person picked, in the person's own timezone.
* ``nutrition_proactive.send_water_reminders`` — a water nudge, at most a
  few times a day, only when intake is behind a *proportional* norm.

Everything else the legacy ``mysite`` nudge package promised (rule
registry, pattern detectors, campaign fan-out) is deliberately absent —
see :mod:`apps.nutrition_proactive.tasks` for why.
"""
