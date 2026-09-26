"""``BotUser.avatar_url`` не уходит на провод — ни одной ручкой (DRF-2520).

# Что было

Докстринг поля обещал, что его рисуют «UI surfaces (mini app, conversation
thread, master-side internal-chat)». Замер 26.09 (``origin/dev`` ``0a0dd57f``):
ни одна ручка поле не отдаёт, ни один экран его не рисует — кружок клиента
стоит на инициалах из ``display_name``, а все ``<img>`` в Mini App — фото
МАСТЕРА из других полей. Обещание снято из модели; этот сторож держит то,
что там теперь написано.

# ЕСЛИ ЭТОТ СТОРОЖ У ВАС ПОКРАСНЕЛ

Вы отдаёте ``avatar_url`` клиенту. Прежде чем продолжать:

1. **Значение — адрес хранилища каталога** (``profile.avatar.url``): MinIO по
   внутреннему имени контейнера, бакет ``public-read``. Телефон по нему
   ничего не загрузит, а адрес, который загрузится, опубликует лицо человека
   любому, кто его знает.
   Замерено на стенде 26.09.2026: настройки prod, переопределений хранилища в
   окружении нет — ``http://minio:9000``, ``custom_domain = None``.
2. **Показ — это прокси через бот с проверкой владения**, как у фото еды
   (DRF-2455: ``apps/miniapp_api/tests/test_food_photo_proxy_2455.py``). Не
   ссылка из зеркала. Прокси для всех фото каталога — DRF-2539.
3. Сделали прокси — впишите файл в ``_ALLOWED`` с номером задачи и поправьте
   докстринг поля в ``apps/identity/models.py``.

# Что считается «отдать»

В коде ручек (``miniapp_api``, ``master_api``, ``admin_api``, без тестов и
миграций): имя ``avatar_url`` или строковый литерал, равный ему целиком (так
выглядят ключ ответа, поле сериализатора, ``values(...)``). Комментарии и
докстринги не считаются: упоминание — не чтение.

# Чего сторож НЕ видит

* Отдачу через ``values()`` / ``model_to_dict`` / ``__all__`` без имени поля.
* Другие приложения: ручки живут в трёх перечисленных, новая папка ручек
  этим обходом не покрыта.
* Общее правило «клиентская поверхность не отдаёт адрес хранилища» — предмет
  DRF-2539 (там все носители: фото мастеров, портфолио). Этот сторож узкий
  нарочно: два сторожа на один предмет начнут делить его.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_API_APPS = ("miniapp_api", "master_api", "admin_api")
_SKIPPED_PARTS = ("migrations", "tests", "__pycache__")
_FIELD = "avatar_url"

#: Известные законные места. Оба читают ``avatar_url`` из ответа КАТАЛОГА о
#: мастере и кладут в ``photo_url`` мастера: это фото мастера, не поле
#: ``BotUser``. Они же — положительная сторона сторожа: обход обязан их найти.
_ALLOWED: dict[str, str] = {
    "apps/master_api/views.py": "фото мастера из ответа каталога (DRF-1813)",
    "apps/master_api/views_profile_card.py": "фото мастера из ответа каталога (DRF-1814)",
}


def _hits(source: str) -> list[int]:
    """Строки, где поле названо кодом: именем или литералом целиком."""
    lines: list[int] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        # Не токенизируется — перестраховаться, а не пропустить молча.
        return [n for n, line in enumerate(source.splitlines(), 1) if _FIELD in line]
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == _FIELD:
            lines.append(tok.start[0])
        elif tok.type == tokenize.STRING and tok.string.strip("rbuRBU") in (
            f'"{_FIELD}"',
            f"'{_FIELD}'",
        ):
            lines.append(tok.start[0])
    return lines


def _scan() -> tuple[int, dict[str, list[int]]]:
    scanned, found = 0, {}
    for app in _API_APPS:
        for path in sorted((REPO_ROOT / "apps" / app).rglob("*.py")):
            if any(part in _SKIPPED_PARTS for part in path.parts):
                continue
            scanned += 1
            hits = _hits(path.read_text(encoding="utf-8", errors="replace"))
            if hits:
                found[path.relative_to(REPO_ROOT).as_posix()] = hits
    return scanned, found


def test_no_endpoint_puts_avatar_url_on_the_wire() -> None:
    scanned, found = _scan()

    # Присутствие — первым и на тех же данных: обход обязан найти оба
    # известных места. Иначе «чужих нет» прошло бы и при ослепшем обходе.
    assert scanned > 20, scanned
    assert sorted(set(found) & set(_ALLOWED)) == sorted(_ALLOWED), found

    offenders = {rel: lines for rel, lines in found.items() if rel not in _ALLOWED}
    assert offenders == {}, (
        f"ручка отдаёт {_FIELD}: {offenders}\n"
        "ПРОЧТИТЕ ДОКСТРИНГ ЭТОГО МОДУЛЯ: значение — внутренний адрес "
        "public-read хранилища; показ — только прокси с проверкой владения (DRF-2455)."
    )


def test_the_guard_actually_fires() -> None:
    """Сторож ловит код и не ловит прозу — проверено здесь же."""
    served = 'def me(u):\n    return {"avatar_url": u.avatar_url}\n'
    assert _hits(served) == [2, 2], "сторож не видит ключ ответа и чтение атрибута"

    serializer = 'class S:\n    fields = ["id", "avatar_url"]\n'
    assert _hits(serializer) == [2], "сторож не видит поле сериализатора"

    prose = '# было: avatar_url\ndef f():\n    """отдаёт avatar_url? нет"""\n    return 1\n'
    assert _hits(prose) == [], "сторож краснеет на упоминании — упоминание не чтение"
