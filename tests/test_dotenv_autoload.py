"""Pin the `.env` autoload contract.

The repo's four Django entry points (``manage.py``, ``config/wsgi.py``,
``config/asgi.py``, ``config/celery.py``) call ``dotenv.load_dotenv()``
before Django reads settings. This file regression-tests two invariants:

    1. Each entry point imports ``load_dotenv`` so the runtime hook is
       there. Catching a developer who removes the call without realising.
    2. The autoloader runs with ``override=False`` so an explicit shell
       export still wins over a ``.env`` entry. Reverse precedence would
       silently mask an operator's deliberate override and is a debugging
       nightmare we don't want to ship.

We don't end-to-end test ``.env`` discovery itself — that's
``python-dotenv``'s job and has its own test suite. We pin the *wiring*.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ENTRY_POINTS = (
    REPO_ROOT / "manage.py",
    REPO_ROOT / "config" / "wsgi.py",
    REPO_ROOT / "config" / "asgi.py",
    REPO_ROOT / "config" / "celery.py",
)


class TestDotenvAutoloadWired:
    def test_each_entry_point_imports_load_dotenv(self):
        for path in ENTRY_POINTS:
            source = path.read_text(encoding="utf-8")
            assert "from dotenv import load_dotenv" in source, (
                f"{path.relative_to(REPO_ROOT)} no longer imports load_dotenv — "
                f"`.env` autoload contract broken."
            )

    def test_each_entry_point_calls_load_dotenv(self):
        for path in ENTRY_POINTS:
            source = path.read_text(encoding="utf-8")
            assert "load_dotenv(" in source, (
                f"{path.relative_to(REPO_ROOT)} imports load_dotenv but does not call it."
            )

    def test_load_dotenv_uses_override_false(self):
        # `override=True` would let `.env` clobber shell-exported vars —
        # surprising precedence; we pin the opposite explicitly.
        for path in ENTRY_POINTS:
            source = path.read_text(encoding="utf-8")
            assert "load_dotenv(override=False)" in source, (
                f"{path.relative_to(REPO_ROOT)} must call "
                f"`load_dotenv(override=False)` so real env vars win."
            )
