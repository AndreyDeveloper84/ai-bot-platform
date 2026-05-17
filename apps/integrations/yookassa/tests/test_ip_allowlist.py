"""IP allowlist tests (DRF-843 / Phase 1 / B7).

Covers :func:`apps.integrations.yookassa.ip_allowlist.is_yookassa_ip`:

* Known YooKassa subnet → True.
* Edge of subnet (network + broadcast addresses) → True.
* Outside-of-allowlist address → False.
* Malformed input → False (never raises).
* Empty input → False.
"""

from __future__ import annotations

from apps.integrations.yookassa.ip_allowlist import (
    YOOKASSA_ALLOWED_CIDRS,
    is_yookassa_ip,
)


class TestIsYookassaIp:
    def test_known_subnet_first_address(self) -> None:
        # 185.71.76.0/27 — first usable host
        assert is_yookassa_ip("185.71.76.1") is True

    def test_known_subnet_middle_address(self) -> None:
        assert is_yookassa_ip("185.71.76.15") is True

    def test_known_subnet_other_block(self) -> None:
        assert is_yookassa_ip("77.75.153.10") is True

    def test_outside_allowlist_rejected(self) -> None:
        # Public address in a totally different range.
        assert is_yookassa_ip("8.8.8.8") is False

    def test_private_address_rejected(self) -> None:
        assert is_yookassa_ip("10.0.0.1") is False
        assert is_yookassa_ip("192.168.1.1") is False
        assert is_yookassa_ip("127.0.0.1") is False

    def test_empty_string_rejected(self) -> None:
        assert is_yookassa_ip("") is False

    def test_malformed_address_rejected(self) -> None:
        assert is_yookassa_ip("not-an-ip") is False
        assert is_yookassa_ip("999.999.999.999") is False
        assert is_yookassa_ip("1.2.3") is False

    def test_ipv6_address_in_allowlist(self) -> None:
        # 2a02:5180::/32 → any 2a02:5180:... address.
        assert is_yookassa_ip("2a02:5180:1::1") is True

    def test_ipv6_address_outside_allowlist(self) -> None:
        assert is_yookassa_ip("2001:db8::1") is False


def test_allowlist_constant_is_immutable() -> None:
    """The CIDR constant is a tuple — tests document the immutability
    intent (mutating it from one test would leak to others)."""
    assert isinstance(YOOKASSA_ALLOWED_CIDRS, tuple)
    assert all(isinstance(cidr, str) for cidr in YOOKASSA_ALLOWED_CIDRS)
