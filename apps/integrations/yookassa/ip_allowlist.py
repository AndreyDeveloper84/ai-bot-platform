"""YooKassa-published webhook source IP allowlist.

### Source

YooKassa publishes the list of subnets they use to deliver webhook
notifications at https://yookassa.ru/developers/using-api/webhooks
(see "Список IP-адресов нотификаций"). The values are stable for
months at a time but DO change. Operators MUST review the published
list quarterly and update :data:`YOOKASSA_ALLOWED_CIDRS` here.

### Why hardcoded vs configurable

A configurable allowlist is one ``.env`` typo away from accepting
``0.0.0.0/0``. Hardcoding the YooKassa-published values forces the
update to go through code review (where a reviewer can compare
against the published list at PR time). Adding a single CIDR is a
one-line PR; that's an acceptable cost for the safety floor.

### Updates required when YooKassa rotates subnets

1. Visit the YooKassa developer-docs page.
2. Replace the contents of :data:`YOOKASSA_ALLOWED_CIDRS` with the
   new set.
3. Bump the date stamp in this docstring and the previous list
   becomes git-history (forensic value for "when did the rotation
   happen?").

Current list snapshot: 2026-05-17. Stays in sync with YooKassa's
published "Список IP-адресов нотификаций" page; ops review quarterly.
"""

from __future__ import annotations

import ipaddress


# YooKassa-published source subnets for webhook deliveries.
# Snapshot: 2026-05-17.
# See module docstring for the update process.
YOOKASSA_ALLOWED_CIDRS: tuple[str, ...] = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "77.75.154.128/25",
    "2a02:5180::/32",
)


def _build_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Pre-parse the CIDR strings once at import time.

    Each request's ``is_yookassa_ip`` call is then a constant-time
    membership check over the parsed networks list — no per-call
    string parsing.
    """
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in YOOKASSA_ALLOWED_CIDRS:
        out.append(ipaddress.ip_network(cidr, strict=False))
    return out


_NETWORKS = _build_networks()


def is_yookassa_ip(remote_addr: str) -> bool:
    """Return True iff ``remote_addr`` falls within a YooKassa subnet.

    Args:
      remote_addr: The source IP as a string. Comes from
                   ``request.META["REMOTE_ADDR"]`` at the view layer.
                   IPv4 and IPv6 are both supported.

    Returns:
      True when the address is within any subnet in
      :data:`YOOKASSA_ALLOWED_CIDRS`. False on:

      * Empty / falsy input.
      * Malformed address (``ipaddress.ip_address`` raises ValueError).
      * Address outside the allowlist.

    Side-effect-free: NO audit row is written on rejection. Per spec,
    we don't want bots discovering valid IPs by probing the audit
    log; the rejection is silent at this layer (the view writes a
    minimal log line at INFO).
    """
    if not remote_addr:
        return False
    try:
        ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(ip in net for net in _NETWORKS)
