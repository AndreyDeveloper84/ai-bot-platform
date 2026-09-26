"""DRF-2555 — две зависимости шифрования полей от SECRET_KEY: одна снята, одна жива.

До листа ADR-0006 обещал ключ в `settings.DJANGO_CRYPTOGRAPHY_KEY`, а
библиотека читает `CRYPTOGRAPHY_KEY`; не задавал её никто. Узла не было —
поэтому обещание прожило. Замер показал, что зависимостей ДВЕ:

1. **ключ AES** — `PBKDF2(CRYPTOGRAPHY_KEY or SECRET_KEY)`
   (`django_cryptography/conf.py`). Лист её снимает: настройка читается из
   `DJANGO_CRYPTOGRAPHY_KEY`;
2. **подпись HMAC** каждого значения — `FernetSigner(key=settings.SECRET_KEY)`,
   сырой (`django_cryptography/core/signing.py`); `decrypt()` проверяет её
   первой. Настройкой не развязывается — это DRF-2562.

Итог, который держат узлы: **SECRET_KEY на сервере с данными не ротируется**,
даже когда `DJANGO_CRYPTOGRAPHY_KEY` задан. Узел «подпись жива» краснеет в
тот день, когда подпись развяжут (DRF-2562) — тогда его надо обратить и
снять запрет в `.env.example`, ADR-0006 и рунбуке выкладки.

Ключ выводится один раз, при загрузке `django_cryptography.conf`. Подмена
`SECRET_KEY` в процессе проверяла бы копию, а не путь, поэтому каждый
сценарий — отдельный процесс Python со своим окружением: Django поднимается
с нуля, библиотека выводит ключ сама, шифрует и расшифровывает тот же
`FernetBytes`, что поля `encrypt(...)`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PLAINTEXT = "память человека: не любит кориандр"

_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
import logging; logging.disable(logging.CRITICAL)
import warnings; warnings.simplefilter("ignore")
import django; django.setup()
from django.conf import settings
from django_cryptography.utils.crypto import FernetBytes
op, payload = sys.argv[1], sys.argv[2]
if op == "aes_key":
    out = bytes(settings.CRYPTOGRAPHY_KEY).hex()
elif op == "encrypt":
    out = FernetBytes().encrypt(payload.encode()).hex()
else:
    try:
        out = FernetBytes().decrypt(bytes.fromhex(payload)).decode()
    except Exception as exc:  # the library raises its own signing / token errors
        out = "<refused:" + type(exc).__name__ + ">"
print("RESULT " + json.dumps(out))
"""

# Только для узла — не настоящие секреты.
SECRET_A = "test-only-secret-key-A-" + "x" * 40
SECRET_B = "test-only-secret-key-B-" + "y" * 40
KEY_K = "test-only-crypto-key-K-" + "z" * 40


def _run(op: str, payload: str, *, secret: str, crypto: str | None) -> str:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DJANGO_CRYPTOGRAPHY_KEY", "CRYPTOGRAPHY_KEY"}
    }
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.local"
    env["DJANGO_SECRET_KEY"] = secret
    if crypto is not None:
        env["DJANGO_CRYPTOGRAPHY_KEY"] = crypto
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, op, payload],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, f"subprocess failed: {proc.stderr[-2000:]}"
    return json.loads(lines[-1][len("RESULT ") :])


def _aes_key(*, secret: str, crypto: str | None) -> str:
    return _run("aes_key", "", secret=secret, crypto=crypto)


def _encrypt(*, secret: str, crypto: str | None) -> str:
    return _run("encrypt", PLAINTEXT, secret=secret, crypto=crypto)


def _decrypt(token: str, *, secret: str, crypto: str | None) -> str:
    return _run("decrypt", token, secret=secret, crypto=crypto)


class TestDependencyOneIsGone:
    """Ключ AES больше не зависит от SECRET_KEY, когда переменная задана."""

    def test_with_the_variable_the_aes_key_ignores_secret_key(self) -> None:
        assert _aes_key(secret=SECRET_A, crypto=KEY_K) == _aes_key(secret=SECRET_B, crypto=KEY_K)

    def test_without_the_variable_the_aes_key_follows_secret_key(self) -> None:
        """Обратный контроль: без переменной всё как было."""
        assert _aes_key(secret=SECRET_A, crypto=None) != _aes_key(secret=SECRET_B, crypto=None)

    def test_first_value_equal_to_current_secret_gives_the_same_aes_key(self) -> None:
        """План владельца, живым выводом библиотеки: значение = текущий
        SECRET_KEY → производный ключ байт в байт тот же, что был без него."""
        before = _aes_key(secret=SECRET_A, crypto=None)
        assert _aes_key(secret=SECRET_A, crypto=SECRET_A) == before
        # Различающая половина: при ДРУГОМ SECRET_KEY ключ AES тот же — значит
        # он пришёл из переменной, а не совпал с SECRET_KEY случайно.
        assert _aes_key(secret=SECRET_B, crypto=SECRET_A) == before

    def test_an_empty_variable_is_the_same_as_unset(self) -> None:
        assert _aes_key(secret=SECRET_A, crypto="") == _aes_key(secret=SECRET_A, crypto=None)


class TestExistingDataStaysReadable:
    def test_rows_written_before_read_after_the_variable_is_set_to_current_secret(self) -> None:
        before = _encrypt(secret=SECRET_A, crypto=None)

        assert _decrypt(before, secret=SECRET_A, crypto=SECRET_A) == PLAINTEXT

    def test_without_the_variable_nothing_changed(self) -> None:
        token = _encrypt(secret=SECRET_A, crypto=None)

        assert _decrypt(token, secret=SECRET_A, crypto=None) == PLAINTEXT


class TestDependencyTwoIsAlive:
    """Подпись по-прежнему на сыром SECRET_KEY — отсюда запрет ротации.

    Если эти узлы однажды покраснеют (расшифровка удалась после смены
    SECRET_KEY), значит подпись развязана (DRF-2562): обратить узлы и снять
    запрет в .env.example, ADR-0006 и docs/runbooks/server-deployment.md.
    """

    def test_rotating_secret_key_breaks_reading_even_with_the_variable_set(self) -> None:
        token = _encrypt(secret=SECRET_A, crypto=KEY_K)

        assert _decrypt(token, secret=SECRET_B, crypto=KEY_K).startswith("<refused:")

    def test_rotating_secret_key_breaks_reading_on_the_owners_plan_too(self) -> None:
        """Ровно тот порядок, который казался безопасным: ключ = прежний
        SECRET_KEY, затем ротация SECRET_KEY → записанное до не читается."""
        before = _encrypt(secret=SECRET_A, crypto=None)

        assert _decrypt(before, secret=SECRET_B, crypto=SECRET_A).startswith("<refused:")
