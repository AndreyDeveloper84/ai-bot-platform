"""Приглашение мастера не шлёт мастеру ничего (решение владельца §44.4).

Этот файл заменил ``test_invite_delivery_honesty.py``, который проверял
ЧЕСТНОСТЬ отчёта о доставке: что владельцу называют причину, по которой
личное сообщение не ушло, а не только пишут её в лог. Отчёт был честный
и бесполезный — сообщение уходило КЛИЕНТСКИМ ботом и достигало только
тот чат, который уже существует, то есть незнакомому мастеру не доходило
никогда, сколько ни чини настройки контура. Владелец салона читал «не
дошло» почти на каждом приглашении.

07.09.2026 владелец убрал попытку целиком: не отправлять и не показывать
строку о результате.

# Почему стража бьёт по исходящему каналу, а не по полям ответа

Полей ``max_dm_delivery`` / ``max_dm_error`` в ответе больше нет, и
проверять их отсутствие — проверять форму, а не поведение. Отправку
можно вернуть, не трогая ответ вовсе: один ``send_message`` в конце
вьюхи, и мастер снова получает (или, чаще, не получает) сообщение,
которого владелец не писал.

Ловится это в двух местах сразу. ``send_message`` — то, что вернут
скорее всего, и подмена его атрибута в модуле — самая читаемая запись
намерения. Но она ловит только вызов через модуль
(``max_outbound.send_message(...)``); ``from ... import send_message``
связал бы имя на импорте и прошёл бы мимо. Поэтому под ним стоит
``httpx.post`` — транспорт, через который уходит ЛЮБОЕ обращение к MAX
из этого процесса, как бы оно ни было импортировано. Ни один другой
путь этого эндпоинта в сеть не ходит, так что молчание транспорта —
утверждение о нём, а не о совпадении.

# Порядок утверждений

Сначала — что приглашение состоялось (201 и токен на месте), потом —
что по дороге никто никому не написал. Без первой половины «ничего не
отправлено» зеленело бы на любом отказе: 400, 403, упавшая миграция.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _body(**over: Any) -> dict[str, Any]:
    return {
        "name": "Анна Петрова",
        "contact_method": "max_username",
        "contact_value": "@anna_styl",
        "services": [],
        "schedule_preset": "default_mon_fri_10_19",
        "mode": "invite",
        **over,
    }


def _post(client: Client, **over: Any):
    return client.post(
        reverse("admin_api:master_invite_create"),
        data=_body(**over),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


class TestNothingIsSentToTheInvitedMaster:
    def test_a_successful_invite_sends_no_message(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"
        settings.MAX_BOT_WEB_APP = "id583403546770_3_bot"

        with (
            patch("apps.channels.max.outbound.send_message") as sent,
            patch.object(httpx, "post") as posted,
        ):
            resp = _post(client)

        # Приглашение состоялось — иначе «ничего не отправлено» ниже
        # доказывало бы только то, что запрос не дошёл до тела вьюхи.
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["invite_token"]

        sent.assert_not_called()
        posted.assert_not_called()

    def test_the_repeat_tap_sends_nothing_either(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """Идемпотентный повтор — отдельная ветка ответа, и она молчит.

        Прежний код на этом пути не отправлял ничего, но ЧИТАЛ прошлый
        исход отправки из аудита и пересказывал его. Теперь читать
        нечего; повтор не должен обнаружить в себе желание «дослать».
        """

        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"

        with (
            patch("apps.channels.max.outbound.send_message") as sent,
            patch.object(httpx, "post") as posted,
        ):
            first = _post(client)
            second = _post(client)

        assert first.status_code == 201, first.content
        assert second.status_code == 200, second.content
        assert second["X-Idempotent"] == "true"
        # Тот же мастер, а не второй — присутствие, на котором держится
        # утверждение об отсутствии отправки.
        assert second.json()["master_id"] == first.json()["master_id"]

        sent.assert_not_called()
        posted.assert_not_called()

    def test_the_envelope_no_longer_carries_a_delivery_verdict(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """Полей о доставке нет — и это часть решения, а не побочный след.

        Пустой всегда вердикт хуже отсутствующего: он приглашает читателя
        что-то с ним делать. Экран мини-аппа читал их обоих.
        """

        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"

        with patch("apps.channels.max.outbound.send_message"):
            resp = _post(client)

        assert resp.status_code == 201, resp.content
        body = resp.json()
        # Присутствие на тех же данных, и именно через `body[...]`:
        # конверт полон и содержит то, ради чего он есть. Без этой пары
        # «поля о доставке отсутствуют» зеленело бы и на пустом теле.
        assert body["master_id"]
        assert body["invite_token"]
        assert body["invite_expires_at"]
        assert "fallback_link" in body
        assert "invite_link" in body

        assert "max_dm_delivery" not in body
        assert "max_dm_error" not in body

    def test_no_dispatch_audit_row_is_written(
        self,
        client: Client,
        owner_bot_user: BotUser,
        tenant: Tenant,
        settings,
    ) -> None:
        """``master.invite_dispatched`` описывал исход отправки.

        Отправки нет — записи тоже. Строка со значением «skipped» была бы
        записью о событии, которого не бывает, и следующий читатель
        аудита сделал бы из неё вывод.
        """

        from apps.audit.models import AuditLog

        settings.SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"

        with patch("apps.channels.max.outbound.send_message"):
            resp = _post(client)

        assert resp.status_code == 201, resp.content
        master_id = resp.json()["master_id"]
        actions = list(
            AuditLog.all_tenants.filter(target_id=master_id).values_list("action", flat=True)
        )
        # Приглашение записано — значит аудит вообще пишется, и пустота
        # ниже не следствие выключенного писателя.
        assert "master.invited" in actions
        assert "master.invite_dispatched" not in actions
