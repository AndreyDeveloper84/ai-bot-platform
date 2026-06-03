"""Регресс-лок (#928): booking-скилл обращается к YClients только под активным,
корректным tenant-контекстом.

Зачем
-----
Тикет #928 (G2.1, ADR-0009 Rule 5) указывает: ``apps.skills.booking`` ходит в
YClients напрямую (минуя канонический gate Ayla). Полное устранение — это
переписывание на Ayla REST (Phase 2.2, см. #996) и оно НЕ делается здесь.

Что проверено при триаже на ``origin/dev``:

* Токены YClients нигде не утекают в логи/тексты ошибок — клиент логирует
  только ``method/url/status`` и ``body_preview`` (тело ОТВЕТА сервера, прогнанное
  через ``_redact_pii``); сами токены живут лишь в ``self._headers`` и наружу не
  выводятся. Поэтому отдельного «затирания» не требуется.
* Креды YClients сейчас НЕ per-tenant — это процессный singleton из ``settings``
  (``YCLIENTS_PARTNER_TOKEN`` и т.п.). Структурный риск writer-multiplicity /
  state-drift остаётся открытым под #996; здесь он НЕ закрывается.

Этот тест НЕ меняет боевой код. Он замораживает существующую неявную границу:
к моменту первого обращения booking-скилла к YClients должен быть активен
tenant-контекст (``current_tenant()``), совпадающий с тенантом разговора. Если
будущий рефактор вынесет вызов YClients из-под tenant-scope (tenant drift), тест
упадёт и привлечёт внимание ревью.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.cache import cache

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext
from apps.skills.booking.skill import BookingSkill
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.models import Tenant

# Переиспользуем тестовые помощники из test_skill, чтобы не дублировать большой
# FakeYClients и хелперы LLM-мока. Импортируются классы/функции, не фикстуры.
from apps.skills.booking.tests.test_skill import (
    FakeYClients,
    _completion,
    _patch_provider_complete,
    _patch_yclients,
    _service,
    _staff,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings) -> Iterator[None]:
    # Та же изоляция, что в test_skill: провайдер резолвится, роутер-кэш чист.
    settings.BASE_DIR = tmp_path
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.CERTIFICATE_PAYMENT_ENABLED = True
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="t928-guard", name="Tenant 928 Guard")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u928",
        chat_id="u928",
        phone="79990000928",
        client_name="Anna",
    )


@pytest.fixture
def context(tenant: Tenant, bot_user: BotUser) -> SkillContext:
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(
        conversation=conv,
        bot_user=bot_user,
        message_text="запиши на массаж",
        trace_id="t-928",
    )


@pytest.mark.django_db(transaction=True)
def test_tenant_context_active_when_yclients_first_called(
    context: SkillContext, tenant: Tenant
) -> None:
    """К моменту первого вызова YClients (prefetch каталога услуг) активен
    tenant-контекст, равный тенанту разговора. Замораживает неявную границу
    из #928 — вызов внешней booking-системы идёт под корректным tenant-scope.
    """

    client = FakeYClients()
    client.services_rows = [_service(22)]
    client.staff_rows = [_staff(11, "Ольга")]

    captured: dict[str, object] = {}
    original_get_services = client.get_services

    def _spy_get_services(*args, **kwargs):
        # Первое обращение booking-скилла к YClients — снимаем активный тенант.
        captured.setdefault("tenant_at_first_call", current_tenant())
        return original_get_services(*args, **kwargs)

    client.get_services = _spy_get_services  # type: ignore[method-assign]

    # LLM не должен делать ничего осмысленного: prefetch YClients происходит
    # ДО tool-loop, поэтому пары пустых ответов с запасом достаточно.
    completions = [_completion(text="чем помочь?")] * 3
    with _patch_yclients(client), _patch_provider_complete(completions):
        with tenant_scope(tenant):
            BookingSkill().handle(context)

    # YClients действительно был дёрнут (иначе тест бессмысленный)...
    assert "tenant_at_first_call" in captured, "booking-скилл не обратился к YClients"
    # ...и сделал это под активным корректным tenant-контекстом.
    assert captured["tenant_at_first_call"] is not None
    assert captured["tenant_at_first_call"] == tenant
    assert captured["tenant_at_first_call"] == context.conversation.tenant
