"""ClientProfile model + signal tests (DRF-527 / Sprint 6 / P1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.identity.models import BotUser, ClientProfile
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="p1-test", name="P1 test")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="42",
        display_name="Test user",
    )


class TestModel:
    def test_profile_auto_created_on_bot_user_save(self, bot_user):
        """P1 signal must populate ClientProfile on BotUser create."""
        assert hasattr(bot_user, "client_profile")
        profile = bot_user.client_profile
        assert profile is not None
        assert profile.tenant == bot_user.tenant

    def test_profile_defaults_to_zero(self, bot_user):
        profile = bot_user.client_profile
        assert profile.recency_days is None
        assert profile.frequency_visits == 0
        assert profile.monetary_total == Decimal("0")
        assert profile.rfm_segment == ""
        assert profile.ltv == Decimal("0")
        assert profile.predicted_ltv_12m == Decimal("0")
        assert profile.churn_risk == 0.0
        assert profile.lifecycle_stage == ""
        assert profile.avg_visit_interval_days is None
        assert profile.loyalty_tier == "bronze"
        assert profile.last_recomputed_at is None
        # Review fields (Gamma #446 review.created handoff) — defaults
        # match «no reviews yet» semantics for fresh profiles.
        assert profile.last_review_rating is None
        assert profile.last_review_at is None
        assert profile.low_rating_flag is False
        assert profile.sentiment_score == 0.0

    def test_signal_idempotent_on_resave(self, bot_user):
        """Re-saving BotUser doesn't create a second profile."""
        bot_user.client_name = "Updated"
        bot_user.save()
        assert ClientProfile.all_tenants.filter(bot_user=bot_user).count() == 1

    def test_str_representation(self, bot_user):
        profile = bot_user.client_profile
        s = str(profile)
        assert "ClientProfile" in s
        assert "bronze" in s

    def test_cascade_delete_on_bot_user(self, bot_user):
        bu_id = bot_user.id
        bot_user.delete()
        assert ClientProfile.all_tenants.filter(bot_user_id=bu_id).count() == 0


class TestReviewFields:
    """Review-mirror fields (Gamma #446 review.created consumer handoff)."""

    def test_review_fields_can_be_set_and_read_back(self, bot_user):
        """Round-trip all 4 fields via consumer-style assignment."""
        from django.utils import timezone

        profile = bot_user.client_profile
        now = timezone.now()
        profile.last_review_rating = 5
        profile.last_review_at = now
        profile.low_rating_flag = False
        profile.sentiment_score = 1.0
        profile.save()

        profile.refresh_from_db()
        assert profile.last_review_rating == 5
        assert profile.last_review_at is not None
        assert profile.low_rating_flag is False
        assert profile.sentiment_score == 1.0

    def test_low_rating_flag_set_with_negative_sentiment(self, bot_user):
        """Consumer pattern: rating <= 2 → low_rating_flag=True + sentiment <= -0.5."""
        from django.utils import timezone

        profile = bot_user.client_profile
        profile.last_review_rating = 2
        profile.last_review_at = timezone.now()
        profile.low_rating_flag = True
        profile.sentiment_score = -0.5
        profile.save()

        profile.refresh_from_db()
        assert profile.last_review_rating == 2
        assert profile.low_rating_flag is True
        assert profile.sentiment_score == -0.5

    def test_sentiment_accepts_full_float_range(self, bot_user):
        """FloatField has no validators — derivation logic lives in Gamma's handler."""
        profile = bot_user.client_profile
        # Edge values from the spec'd derivation table: 5→1.0 / 1→-1.0.
        profile.sentiment_score = 1.0
        profile.save()
        profile.refresh_from_db()
        assert profile.sentiment_score == 1.0

        profile.sentiment_score = -1.0
        profile.save()
        profile.refresh_from_db()
        assert profile.sentiment_score == -1.0


class TestTenantProtect:
    def test_tenant_drop_blocked_when_profiles_exist(self, tenant, bot_user):
        """tenant FK PROTECT — accidental drop must not vapourise derived state."""
        from django.db.models import ProtectedError

        # bot_user creates profile via signal. Tenant.delete should fail.
        with pytest.raises(ProtectedError):
            tenant.delete()

    def test_tenant_drop_works_after_explicit_profile_delete(self, tenant, bot_user):
        # Operator deletes profile explicitly first, then tenant succeeds.
        ClientProfile.all_tenants.filter(tenant=tenant).delete()
        bot_user.delete()
        tenant.delete()
        assert Tenant.objects.filter(slug="p1-test").count() == 0


class TestTenantScoping:
    def test_default_manager_filters_to_current_tenant(self, tenant, bot_user):
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            assert ClientProfile.objects.count() == 1
        # Outside tenant_scope — strict scope returns empty by default.
        assert ClientProfile.all_tenants.count() == 1

    def test_other_tenant_invisible_in_scope(self, tenant, bot_user):
        from apps.tenancy.context import tenant_scope

        other = Tenant.objects.create(slug="p1-other", name="Other")
        with tenant_scope(other):
            assert ClientProfile.objects.filter(bot_user=bot_user).count() == 0


class TestAdmin:
    def test_admin_class_is_read_only(self):
        from apps.identity.admin import ClientProfileAdmin

        admin = ClientProfileAdmin(ClientProfile, None)
        assert admin.has_add_permission(None) is False
        assert admin.has_change_permission(None) is False
        assert admin.has_delete_permission(None) is False

    def test_admin_list_display_includes_segment_and_tier(self):
        from apps.identity.admin import ClientProfileAdmin

        admin = ClientProfileAdmin(ClientProfile, None)
        assert "rfm_segment" in admin.list_display
        assert "loyalty_tier" in admin.list_display
        assert "lifecycle_stage" in admin.list_display


class TestBookingCompletedSignal:
    """P8 wires a real handler; P1 ships the signal definition + smoke-test."""

    def test_signal_can_be_fired(self, bot_user):
        from apps.identity.signals import booking_completed

        # No-op handlers; we're just verifying the signal exists + accepts kwargs.
        received: list[dict] = []

        def handler(sender, **kwargs):
            received.append(kwargs)

        booking_completed.connect(handler)
        try:
            booking_completed.send(sender=None, bot_user=bot_user, amount=Decimal("1000"))
            assert len(received) == 1
            assert received[0]["bot_user"] == bot_user
        finally:
            booking_completed.disconnect(handler)


@pytest.mark.django_db
class TestTimezoneValidation:
    """`PATCH /me` перестаёт принимать что попало за часовой пояс (DRF-1477).

    До этой проверки колонку принимала ЛЮБАЯ строка: она обрезалась до
    64 символов и уходила в базу. Значит `«не знаю»` доезжало до
    `nutrition_proactive.prefs.resolve_timezone`, где `_safe_zoneinfo`
    возвращал `None`, и человек **молча** уезжал на пояс салона.

    Ошибка не сообщалась никому и никогда: ни тому, кто прислал, ни
    тому, кто читает. Теперь она сообщается тому, кто прислал.
    """

    def test_a_real_zone_is_stored(self, bot_user):
        from apps.identity.services.profile import update_profile

        # Положительная стража впереди: до записи пояса нет.
        assert bot_user.timezone == ""

        snap = update_profile(bot_user, {"timezone": "Asia/Yekaterinburg"})

        assert snap.timezone == "Asia/Yekaterinburg"
        bot_user.refresh_from_db()
        assert bot_user.timezone == "Asia/Yekaterinburg"

    def test_garbage_is_refused_loudly_instead_of_sliding_to_the_salon(self, bot_user):
        from apps.identity.services.profile import ProfileUpdateError, update_profile

        with pytest.raises(ProfileUpdateError) as exc:
            update_profile(bot_user, {"timezone": "не знаю"})

        # Причина названа, а не спрятана: чинит тот, кто прислал.
        assert "IANA" in str(exc.value)
        bot_user.refresh_from_db()
        # И колонка НЕ тронута — отказ не оставляет половины записи.
        assert bot_user.timezone == ""

    def test_a_plausible_but_nonexistent_zone_is_refused_too(self, bot_user):
        """`Europe/Moskva` выглядит как пояс и им не является."""
        from apps.identity.services.profile import ProfileUpdateError, update_profile

        with pytest.raises(ProfileUpdateError):
            update_profile(bot_user, {"timezone": "Europe/Moskva"})

    def test_empty_is_allowed_because_it_means_unset(self, bot_user):
        """Пустота — законный ответ «не задано» (DRF-1606).

        Отклонять её значило бы запретить СНЯТЬ пояс, однажды
        поставленный по ошибке.
        """
        from apps.identity.services.profile import update_profile

        update_profile(bot_user, {"timezone": "Europe/Moscow"})
        bot_user.refresh_from_db()
        # Положительная стража впереди: было что снимать.
        assert bot_user.timezone == "Europe/Moscow"

        snap = update_profile(bot_user, {"timezone": ""})

        assert snap.timezone == ""
        bot_user.refresh_from_db()
        assert bot_user.timezone == ""

    def test_the_refusal_does_not_touch_the_neighbouring_field(self, bot_user):
        """Отказ по поясу не должен записать имя из того же запроса.

        `client_name` и `timezone` приходят одним запросом. Пока
        проверка стояла ВНУТРИ цикла записи, имя успевало присвоиться
        экземпляру до того, как пояс отвергнут: до базы это не доезжало
        только потому, что `save()` стоит ниже цикла — гарантия держалась
        на порядке строк, а не на устройстве.

        Проверка вынесена вперёд записи, и тест сторожит именно это:
        отказ не оставляет НИ отправленной половины, НИ испорченного
        экземпляра в памяти.
        """
        from apps.identity.services.profile import ProfileUpdateError, update_profile

        with pytest.raises(ProfileUpdateError):
            update_profile(bot_user, {"client_name": "Аня", "timezone": "мусор"})

        # Экземпляр в памяти чист — проверяем ДО `refresh_from_db()`,
        # иначе перечитывание скрыло бы порчу, которую мы и сторожим.
        assert bot_user.client_name != "Аня"
        bot_user.refresh_from_db()
        assert bot_user.client_name != "Аня"
        assert bot_user.timezone == ""
