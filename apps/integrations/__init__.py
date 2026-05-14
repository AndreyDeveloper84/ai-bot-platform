"""External-service integrations.

One sub-package per upstream system. Each sub-package owns an HTTP client,
DTOs, and the platform-side breaker + retry policy for that service. No
cross-talk between sub-packages — feature layers compose them.

Current:
* ``ayla`` — Ayla nutrition backend (food recognition, water diary, profile
  norms). Sprint 9 I1 (DRF-825).
"""
