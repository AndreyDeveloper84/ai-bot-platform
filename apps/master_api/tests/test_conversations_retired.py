"""Бэкенд переписки мастера с клиентом снят — DRF-1528 (ruling владельца 06.09).

Поверхность сняли в DRF-1255 (#1921): экраны удалены, клиент API без ручек.
Под ними оставался HTTP-доступ, и владелец назвал причину прямо: открытые
ручки — способ обойти DRF-1039 («телефон клиента исполнителю не передаётся»)
в обход поверхности, а мёртвый код с живым дефектом (DRF-1119: ответ мастера
сохраняется, но не отправляется) никем не проверяется, потому что его никто
не видит.

Граница ruling'а: **удаляется код, не данные.** Строки `Conversation`,
`Message`, `AiDraft` остаются — их читают переписка клиента с ассистентом,
adminconsole, follow-up'ы, каналы и консьюмеры. Ни `DELETE`, ни миграции.

Девять снятых маршрутов отвечают 410 Gone (прецедент — `yookassa_retired`,
410 ради громкости в логах и RFC 9110 §15.5.11), а не 404 и тем более не 500.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.conversations.models import AiDraft, Conversation, Message
from apps.conversations.services import record_message
from apps.identity.models import BotUser
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CONV = uuid.uuid4()
DRAFT = uuid.uuid4()

#: Девять ручек: путь и метод, которыми их звал снятый экран.
RETIRED: tuple[tuple[str, str], ...] = (
    ("/api/v1/master/conversations", "get"),
    (f"/api/v1/master/conversations/{CONV}", "get"),
    (f"/api/v1/master/conversations/{CONV}/messages", "post"),
    (f"/api/v1/master/conversations/{CONV}/mark-read", "post"),
    (f"/api/v1/master/conversations/{CONV}/promote", "post"),
    (f"/api/v1/master/conversations/{CONV}/drafts/generate", "post"),
    (f"/api/v1/master/conversations/{CONV}/drafts/{DRAFT}/send-as-me", "post"),
    (f"/api/v1/master/conversations/{CONV}/drafts/{DRAFT}/release-to-ai", "post"),
    # Список с фильтром — тот же маршрут, но так его звал экран: строка
    # запроса не должна менять ответ.
    ("/api/v1/master/conversations?filter=active", "get"),
)


@pytest.fixture()
def linked_master(db) -> tuple[Tenant, str]:
    tenant = Tenant.objects.create(name="Формула тела", slug="formula")
    make_master(tenant, name="Анна Петрова")
    return tenant, init_data_header()


class TestRetiredEndpoints:
    def test_all_nine_answer_410_not_404_and_never_500(
        self, linked_master: tuple[Tenant, str]
    ) -> None:
        """Снятая ручка отвечает «ушло», а не «сломалось» и не «нет такого».

        500 здесь опаснее всего: он читается как временная авария и зовёт
        повторить запрос, хотя ручки больше нет и не будет.
        """
        _tenant, header = linked_master
        client = Client()
        seen: list[tuple[str, int]] = []
        for path, method in RETIRED:
            response = getattr(client, method)(path, HTTP_AUTHORIZATION=header)
            seen.append((path, response.status_code))
        assert [code for _p, code in seen] == [410] * len(RETIRED), seen

    def test_the_answer_says_why_and_names_the_ruling(
        self, linked_master: tuple[Tenant, str]
    ) -> None:
        """Тело ответа объясняет причину — иначе 410 читается как сбой."""
        _tenant, header = linked_master
        response = Client().get("/api/v1/master/conversations", HTTP_AUTHORIZATION=header)
        body = response.json()
        assert body.get("error") == "master_client_chat_retired", body
        assert "OD-7" in body.get("detail", ""), body

    def test_no_auth_still_410_not_401(self, linked_master: tuple[Tenant, str]) -> None:
        """Снятая ручка снята для всех: разбирать личность уже незачем."""
        response = Client().get("/api/v1/master/conversations")
        assert response.status_code == 410, response.status_code

    def test_route_names_are_gone_from_the_url_table(self) -> None:
        """Имена маршрутов сняты — `reverse` их больше не знает."""
        for name in (
            "conversations_list",
            "conversation_detail",
            "conversation_send_message",
            "conversation_mark_read",
            "conversation_promote",
            "conversation_draft_generate",
            "conversation_draft_send_as_me",
            "conversation_draft_release_to_ai",
        ):
            with pytest.raises(NoReverseMatch):
                reverse(f"master_api:{name}")


class TestCodeIsGoneNotJustUnrouted:
    def test_service_modules_deleted(self) -> None:
        """Модули сервисов удалены, а не оставлены «на всякий случай».

        Оставленный модуль — это HTTP-доступ, который вернут одной строкой
        в urls.py, и дефект DRF-1119 вместе с ним.
        """
        import importlib

        for module in (
            "apps.master_api.services.conversations",
            "apps.master_api.services.conversation_detail",
            "apps.master_api.services.ai_drafts",
        ):
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(module)

    def test_dialogue_reader_registry_has_no_rows_for_deleted_services(self) -> None:
        """Реестр стражей стирания не ссылается на удалённое.

        Строки жили ради доказательства «этот читатель чтит cutoff». Читателя
        нет — доказательство должно говорить о том, что есть, иначе сторож
        зелёный, а объяснение в нём пережило свою причину.
        """
        from apps.conversations.dialogue_readers import DIALOGUE_READERS

        deleted = (
            "apps.master_api.services.conversations",
            "apps.master_api.services.conversation_detail",
            "apps.master_api.services.ai_drafts",
        )
        stale = [key for key in DIALOGUE_READERS if key.split(":")[0] in deleted]
        assert stale == [], stale
        # Строки про `services.dashboard` остаются намеренно: дашборд не
        # удаляется, его читатели переписки живы и классифицированы.


class TestDataUntouched:
    def test_rows_survive_the_retired_endpoints(self, linked_master: tuple[Tenant, str]) -> None:
        """Ruling 06.09: «Данные существующих диалогов не удалять».

        Счётчик до и после обращения ко всем снятым ручкам: ни одна из них
        не имеет права ничего удалить по дороге.
        """
        _tenant, header = linked_master
        before = (
            Conversation.all_tenants.count(),
            Message.all_tenants.count(),
            AiDraft.all_tenants.count(),
        )
        client = Client()
        for path, method in RETIRED:
            getattr(client, method)(path, HTTP_AUTHORIZATION=header)
        after = (
            Conversation.all_tenants.count(),
            Message.all_tenants.count(),
            AiDraft.all_tenants.count(),
        )
        assert after == before, (before, after)


class TestAutoDraftWireIsCut:
    """Провод из канонической записи сообщения в снятый таск.

    `record_message` на каждое входящее сообщение клиента ставил в
    очередь `master_api.tasks.auto_generate_draft_for_inbound` — таск,
    который звал `ai_drafts.generate_draft_for_conversation`. Оставить
    провод и удалить модуль значило бы уронить `on_commit` на КАЖДОМ
    входящем сообщении любого диалога, включая переписку клиента с
    ассистентом: ImportError после коммита, уже за пределами try
    обработчика канала.
    """

    def test_the_task_module_is_gone(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("apps.master_api.tasks")

    @pytest.mark.django_db(transaction=True)
    def test_storing_an_inbound_message_enqueues_nothing(self) -> None:
        """Положительная пара: сообщение по-прежнему сохраняется.

        `transaction=True` — иначе `on_commit` откатится вместе с тестом
        и «ничего не упало» ничего не значило бы: именно в этом режиме
        прежний провод и звал брокера (у которого в тестах нет адреса).
        """

        tenant = Tenant.objects.create(name="Формула тела", slug="formula-wire")
        customer = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="wire-customer-1",
            chat_id="wire-customer-1",
            display_name="Ксения",
        )
        with tenant_scope(tenant):
            conversation = Conversation.all_tenants.create(
                tenant=tenant, bot_user=customer, is_active=True
            )
            message = record_message(
                conversation,
                role=Message.Role.USER,
                content="Здравствуйте, хочу записаться",
            )
            assert Message.all_tenants.filter(pk=message.pk).exists()


class TestStudioChatStillWorks:
    """Парная положительная стража: «Со студией» — переписка мастера с САЛОНОМ.

    Под запрет OD-7 она не подпадает (текст DRF-1255) и обязана работать
    полностью. Здесь — структурная половина: маршруты на месте и ведут в
    свой модуль. Функциональная половина живёт там, где для неё уже есть
    стенд (бот-токен, слаг тенанта, привязанный BotUser) —
    ``apps/internal_chat/tests/test_master_views.py``; дублировать его
    фикстуры здесь значило бы завести второй стенд того же канала.
    """

    def test_studio_chat_routes_are_mounted(self) -> None:
        for name in (
            "master_threads",
            "master_thread_detail",
            "master_send_message",
            "master_mark_read",
        ):
            try:
                url = reverse(f"internal_chat:{name}", args=[uuid.uuid4()])
            except NoReverseMatch:
                url = reverse(f"internal_chat:{name}")
            assert url.startswith("/api/v1/internal-chat/"), url

    def test_studio_chat_module_untouched(self) -> None:
        import importlib

        module = importlib.import_module("apps.internal_chat.views")
        assert hasattr(module, "master_threads_collection"), dir(module)
