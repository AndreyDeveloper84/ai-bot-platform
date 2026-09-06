"""AI dietologist — coaching layer on top of Ayla's diary and goal layer (DRF-1464).

Foundation slice (T1–T3): the two safety switches (:mod:`.flags`), the
active-goal reader (:mod:`.goals`) and the week-picture adapter
(:mod:`.history`). No sending surface lives here yet — the proactive
mechanics are DRF-1468's (:mod:`apps.nutrition_proactive.antinag`), and
this package deliberately carries no frequency counters of its own.
"""
