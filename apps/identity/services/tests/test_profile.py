"""Profile service tests — legacy account-delete erasure (DRF-956)."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.identity.models import BotUser, UserPreferences
from apps.identity.services.profile import (
    DELETE_CONFIRMATION_TOKEN,
    DeletionConfirmationMismatch,
    soft_delete_user,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="profile-test", name="Profile Test")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=uuid.uuid4(),
        phone="+79991234567",
        client_name="Мария Иванова",
        display_name="Мария",
        avatar_url="https://cdn.example/avatars/123.png",
        context={"city": "Penza"},
    )


@pytest.fixture
def other_bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="99999",
        chat_id="99999",
        ayla_user_id=uuid.uuid4(),
        phone="+79998887766",
        client_name="Другой Пользователь",
        display_name="Другой",
        avatar_url="https://cdn.example/avatars/999.png",
        context={"city": "Moscow"},
    )


class TestSoftDeleteUser:
    def test_confirmed_delete_erases_all_pii(self, bot_user):
        UserPreferences.all_tenants.create(
            tenant=bot_user.tenant,
            bot_user=bot_user,
            birthday_date="1990-01-01",
        )
        before = timezone.now()

        soft_delete_user(bot_user, DELETE_CONFIRMATION_TOKEN)

        bot_user.refresh_from_db()
        assert bot_user.deleted_at is not None
        assert bot_user.deleted_at >= before
        assert bot_user.phone == ""
        assert bot_user.client_name == ""
        assert bot_user.display_name == ""
        assert bot_user.avatar_url == ""
        assert bot_user.context == {}
        assert not UserPreferences.all_tenants.filter(bot_user=bot_user).exists()

    def test_wrong_confirmation_raises_and_does_not_mutate(self, bot_user):
        original_phone = bot_user.phone
        original_display_name = bot_user.display_name
        original_avatar_url = bot_user.avatar_url
        original_context = bot_user.context

        with pytest.raises(DeletionConfirmationMismatch):
            soft_delete_user(bot_user, "НЕПРАВИЛЬНЫЙ")

        bot_user.refresh_from_db()
        assert bot_user.deleted_at is None
        assert bot_user.phone == original_phone
        assert bot_user.display_name == original_display_name
        assert bot_user.avatar_url == original_avatar_url
        assert bot_user.context == original_context

    def test_repeat_delete_is_idempotent(self, bot_user):
        soft_delete_user(bot_user, DELETE_CONFIRMATION_TOKEN)
        bot_user.refresh_from_db()
        first_deleted_at = bot_user.deleted_at

        soft_delete_user(bot_user, DELETE_CONFIRMATION_TOKEN)

        bot_user.refresh_from_db()
        assert bot_user.deleted_at == first_deleted_at
        assert bot_user.display_name == ""
        assert bot_user.avatar_url == ""

    def test_does_not_touch_unrelated_user(self, bot_user, other_bot_user):
        soft_delete_user(bot_user, DELETE_CONFIRMATION_TOKEN)

        other_bot_user.refresh_from_db()
        assert other_bot_user.deleted_at is None
        assert other_bot_user.phone == "+79998887766"
        assert other_bot_user.display_name == "Другой"
        assert other_bot_user.avatar_url == "https://cdn.example/avatars/999.png"
        assert other_bot_user.context == {"city": "Moscow"}
