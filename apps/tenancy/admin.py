"""Admin registration for Tenant (DRF-419 / Sprint 1 / A2).

Ported from Ayla ``origin/dev:tenants/admin.py``. Stripped of the
Unfold theming dependency — the platform admin is plain Django for
Sprint 1; we can layer Unfold in later if/when an admin polish sprint
arrives.

Key behaviour:
  * ``get_queryset`` uses ``Tenant.all_objects`` so deactivated tenants
    remain visible in admin (default ``Tenant.objects`` manager hides
    ``is_active=False`` rows from app code).
  * ``id``, ``created_at``, ``updated_at`` are read-only — the UUID is
    auto-generated and the timestamps are managed by ``auto_now*``.
  * Rows with ``is_system=True`` are protected from deletion (KB-RAG Sub-1,
    GH #114) — the ``global_kb`` corpus tenant must not vanish on a stray
    admin click. ``has_delete_permission`` hides the per-row delete button,
    ``delete_model`` and ``delete_queryset`` enforce the same rule
    server-side in case a custom action bypasses the UI.
  * Секреты Telegram (``telegram_bot_token``, ``telegram_webhook_secret``)
    в форму не отдаются — см. :class:`TenantAdminForm` (DRF-1495).
  * Штатной формы «Add Tenant» у салона НЕТ (``has_add_permission``):
    она физически не может принять Ayla-UUID и рождает нерабочий салон
    молча. Единственный путь — экран «Подключить салон» (``connect/``).
  * Карточка подключённого салона несёт то же действие верификации
    мастеров, что экран подключения, — ``change_view`` перехватывает
    POST по :data:`TenantAdmin.VERIFY_FIELD`.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html, format_html_join

from apps.adminconsole.theme import AylaAdminMedia
from apps.tenancy.models import Tenant
from apps.tenancy.onboarding import (
    REASON_LABELS,
    ConnectError,
    ConnectPending,
    assess_salon,
    connect_salon,
    verify_salon_masters,
)


class TenantAdminForm(forms.ModelForm):
    """Форма тенанта, которая не показывает секреты (DRF-1495).

    До этой правки маскировалась только колонка списка
    (``telegram_bot_token_masked``), а форма изменения отдавала и токен
    бота, и вебхук-секрет обычными текстовыми полями: полные значения
    уезжали в HTML страницы каждому, кто её открыл, и оставались в
    кеше браузера, в скриншоте, в «сохранить страницу». Границы эпика
    DRF-75 говорят: секреты и токены в интерфейсе не показывать никак.

    Механика простая и без своего состояния: поле рендерится
    ``PasswordInput(render_value=False)`` — значение в разметку не
    попадает вовсе, — и пустая отправка означает «не менять». Задать
    новое значение по-прежнему можно, стереть — нет; стирание секрета
    это редкая осознанная операция, и для неё есть shell.

    Что именно сейчас настроено, видно по read-only полям
    ``telegram_bot_token_state`` / ``telegram_webhook_secret_state``:
    они отвечают «задан / не задан» (для токена — плюс последние 4
    символа, чтобы отличить два бота друг от друга, не раскрывая
    ключа).
    """

    telegram_bot_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Новый токен бота",
        help_text=(
            "Пусто — оставить текущий. Значение не показывается: это "
            "credential BotFather. Текущее состояние — в поле выше."
        ),
    )
    telegram_webhook_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Новый вебхук-секрет",
        help_text=(
            "Пусто — оставить текущий. Значение не показывается. "
            "Генерируется оператором через secrets.token_urlsafe(32)."
        ),
    )

    class Meta:
        model = Tenant
        fields = "__all__"

    def clean_telegram_bot_token(self) -> str:
        submitted = (self.cleaned_data.get("telegram_bot_token") or "").strip()
        return submitted or (self.instance.telegram_bot_token or "")

    def clean_telegram_webhook_secret(self) -> str:
        submitted = (self.cleaned_data.get("telegram_webhook_secret") or "").strip()
        return submitted or (self.instance.telegram_webhook_secret or "")


class SalonConnectForm(forms.Form):
    """Поля экрана «подключить салон» (DRF-1525).

    Три поля и кнопка. Поля «Ayla Tenant UUID» здесь больше нет (владелец,
    11.09.2026): идентификатор салона приходит из каталога по slug —
    :func:`connect_salon` находит салон в Ayla или заводит его и берёт
    UUID оттуда. Человек его не вводит и не видит; вводимый «на глаз»
    ключ и был источником класса DRF-1510.
    """

    slug = forms.SlugField(
        max_length=50,
        label="Slug",
        help_text=(
            "Строчные буквы, цифры, дефис/подчёркивание, 2–50 знаков. Тот же "
            "slug будет у салона в Ayla: если он там уже есть — салон "
            "найдётся, если нет — будет заведён."
        ),
    )
    name = forms.CharField(
        max_length=200,
        label="Название салона",
        help_text="Как в Ayla, если салон там уже заведён: другое название по тому же slug — отказ.",
    )
    city = forms.CharField(
        max_length=120,
        label="Город",
        help_text="Например, «Пенза». Без города салон отсутствует во всех городских ответах.",
    )


@admin.register(Tenant)
class TenantAdmin(AylaAdminMedia, admin.ModelAdmin):
    form = TenantAdminForm
    change_list_template = "admin/tenancy/tenant/change_list.html"
    list_display = (
        "name",
        "slug",
        "city",
        # Признака связи с Ayla в списке не было вовсе, и мёртвый салон
        # 09.09.2026 стоял среди живых неотличимо: те же имя, slug и
        # город, та же дата создания. Колонка читает ТОЛЬКО локальные
        # поля — по строке на салон, никаких запросов в Ayla: список
        # рисуется целиком, и один сетевой вызов на строку превратил бы
        # его в минуту ожидания.
        "ayla_link_state",
        "is_active",
        "is_system",
        "shadow_mode",
        "telegram_bot_token_masked",
        "daily_token_cap",
        "daily_cost_cap_usd",
        "created_at",
    )
    list_filter = ("is_active", "is_system", "shadow_mode")
    # Оставлено как было. Единственное правимое прямо из списка поле —
    # переключатель «бот не пишет клиенту». Подпись у него теперь
    # называет последствие, а не термин: «Shadow mode» не говорил
    # оператору ничего, «Теневой режим: бот не пишет клиенту» говорит.
    list_editable = ("shadow_mode",)
    search_fields = ("name", "slug")
    search_help_text = "Ищет по названию салона и его коду."
    empty_value_display = "нет данных"
    readonly_fields = (
        "id",
        "is_system",
        "created_at",
        "updated_at",
        "telegram_bot_token_state",
        "telegram_webhook_secret_state",
        "salon_visibility_state",
        "last_catalog_sync_at",
        "last_catalog_sync_ok_at",
    )
    fieldsets = (
        (
            "Салон",
            {
                "fields": ("id", "slug", "name", "city", "address", "is_active", "is_system"),
                "description": (
                    "Город участвует в городском поиске: без него салон "
                    "отсутствует во всех городских ответах (DRF-1510). "
                    "Адрес (DRF-1588) синхронизация заполнит сама, когда "
                    "источник начнёт его отдавать (DRF-1587); пустое поле "
                    "означает «источник ничего не сказал», а не «адреса нет»."
                ),
            },
        ),
        (
            "Виден ли салон клиентам",
            {
                "fields": (
                    "salon_visibility_state",
                    "last_catalog_sync_ok_at",
                    "last_catalog_sync_at",
                ),
                "description": (
                    "Проверка исхода: салон заведён тогда, когда виден "
                    "клиенту, а не когда появилась строка. Причины "
                    "невидимости — закрытый перечень DRF-1511."
                ),
            },
        ),
        (
            "Куда бот пишет менеджеру салона",
            {
                "fields": ("manager_user_id", "manager_chat_id"),
                "description": (
                    "Сюда уходят эскалации напоминаний, оповещения о записях, "
                    "просьбы изменить расписание и предупреждения о лимитах. "
                    "<b>Заполняйте manager_user_id.</b> chat_id — это "
                    "идентификатор ДИАЛОГА: он верен только для того бота, из "
                    "переписки с которым его скопировали, и салонный бот, "
                    "отправляя туда, получает 404 dialog.not.found (замер "
                    "07.09.2026, OPEN_DECISIONS §55). user_id — это человек, и "
                    "он верен для любого нашего бота. "
                    "Пересчитать одно в другое нельзя: MAX отдаёт "
                    "recipient.user_id в ответе на успешную отправку — оттуда "
                    "и берут. Пока user_id пуст, читается chat_id, то есть "
                    "поведение остаётся вчерашним."
                ),
            },
        ),
        (
            "Теневой режим",
            {
                "fields": ("shadow_mode",),
                "description": (
                    "Включено — оркестратор пишет теневые строки, но НЕ "
                    "отправляет клиенту ни одного сообщения. Это "
                    "переключатель поведения бота, а не оформления: перед "
                    "включением на бою читайте "
                    "docs/runbooks/shadow-mode-launch.md."
                ),
            },
        ),
        (
            "Канал Telegram",
            {
                "fields": (
                    "telegram_bot_token_state",
                    "telegram_bot_token",
                    "telegram_webhook_secret_state",
                    "telegram_webhook_secret",
                ),
                "description": (
                    "Ключи телеграм-бота этого салона. Токен выдаёт "
                    "@BotFather; вебхук-секрет оператор генерирует сам "
                    "(<code>secrets.token_urlsafe(32)</code>) и "
                    "регистрирует в setWebhook. "
                    "DRF-1495: ни одно из двух значений в эту форму не "
                    "отдаётся. Поля ввода пустые всегда; пустая отправка "
                    "означает «оставить как есть». Что настроено сейчас — "
                    "в строках состояния над каждым полем. "
                    "См. docs/runbooks/telegram-bot-onboarding.md."
                ),
            },
        ),
        (
            "Дневные лимиты расходов на модель",
            {
                "fields": ("daily_token_cap", "daily_cost_cap_usd"),
                "description": (
                    "Дневной потолок расходов салона на модель. Любой из "
                    "двух лимитов срабатывает сам по себе: как только он "
                    "исчерпан, бот отвечает клиенту заглушкой «лимит "
                    "исчерпан». Счётчики обнуляются в 00:00 UTC. На 80 % "
                    "бот один раз в сутки пишет менеджеру салона — адрес "
                    "берётся из раздела «Куда бот пишет менеджеру салона»."
                ),
            },
        ),
        (
            "Служебное",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
                "description": "Отметки времени. Проставляются сами, править нечего.",
            },
        ),
    )

    @admin.display(description="Токен Telegram (последние 4)")
    def telegram_bot_token_masked(self, obj: Tenant) -> str:
        """Admin column: never expose the full Telegram bot token.

        Delegates to :meth:`Tenant._mask_telegram_token` so the masking
        rule lives in one place (model + admin can't drift).
        """
        return obj._mask_telegram_token() or "—"

    @admin.display(description="Токен бота сейчас")
    def telegram_bot_token_state(self, obj: Tenant) -> str:
        """Состояние токена без самого токена (DRF-1495).

        Последние 4 символа — тот же приём, что в списке и в
        ``__repr__``: их хватает, чтобы отличить два бота друг от друга,
        и не хватает, чтобы воспользоваться ключом.
        """
        masked = obj._mask_telegram_token()
        return f"задан ({masked})" if masked else "не задан"

    @admin.display(description="Вебхук-секрет сейчас")
    def telegram_webhook_secret_state(self, obj: Tenant) -> str:
        """«Задан / не задан». Хвост не показываем: секрет сравнивается
        целиком через ``hmac.compare_digest``, и любая его часть — подсказка.
        """
        return "задан" if (obj.telegram_webhook_secret or "") else "не задан"

    @admin.display(description="Связь с Ayla")
    def ayla_link_state(self, obj: Tenant) -> str:
        """Связан ли салон с Ayla — колонка списка.

        Три ответа, а не два. «Синхронизация шла и не удалась» и
        «синхронизация не подходила ни разу» — разные состояния, и
        слить их в одно «не связан» значило бы спрятать ровно тот
        случай, ради которого колонка заведена: у мёртвого салона
        ``last_catalog_sync_at`` тоже NULL, то есть к нему не приходили
        вовсе, а не приходили и не смогли.

        Здесь НЕ спрашивается Ayla: список рисует все строки разом.
        Настоящую причину («идентификатора нет в Ayla») называет
        карточка одного салона — там один запрос уместен, здесь их было
        бы столько же, сколько салонов.
        """
        if obj.last_catalog_sync_ok_at is not None:
            return "связан"
        if obj.last_catalog_sync_at is not None:
            return "нет связи — синхронизация шла, но ни разу не удалась"
        return "нет связи — синхронизация ни разу не подходила"

    def _ayla_probe_client(self):  # type: ignore[no-untyped-def]
        """Клиент, которым карточка спрашивает Ayla про идентификатор.

        Отдельный метод, а не строка внутри :meth:`salon_visibility_state`:
        тесты подменяют его, чтобы проверять разметку экрана, а не работу
        HTTP-клиента (у него свои тесты). Ввоз локальный — ``admin.py``
        грузится при старте, а клиент тянет за собой настройки синхронизации.
        """
        from apps.catalog.services.http_client import CatalogHttpClient

        return CatalogHttpClient()

    @admin.display(description="Видимость и состояние каталога")
    def salon_visibility_state(self, obj: Tenant) -> str:
        """Карточка одного салона: синхронизация, витрина, причины (DRF-1525).

        Не сводка по контуру (это DRF-1500), а ответ про этот салон:
        когда последний раз синхронизировался, сколько услуг и
        бронируемых мастеров, и — если клиент его не видит — почему,
        названной причиной из закрытого перечня DRF-1511.

        Клиент Ayla передаётся намеренно: без него карточка мёртвого
        салона говорила «синхронизация ни разу не проходила», что
        читается как «запустите синхронизацию», хотя по этому UUID она
        вернёт ноль всегда. Запрос уходит ТОЛЬКО когда успешной
        синхронизации не было ни разу, и его отказ причину не меняет
        (:func:`~apps.tenancy.onboarding._never_synced_reason`).
        """
        assessed = assess_salon(obj, http_client=self._ayla_probe_client())
        last_ok = (
            assessed.last_sync_ok_at.strftime("%d.%m.%Y %H:%M UTC")
            if assessed.last_sync_ok_at
            else "никогда"
        )
        head = format_html(
            "Последняя успешная синхронизация: {}. Активных услуг: {}. Бронируемых мастеров: {}.",
            last_ok,
            assessed.active_services,
            assessed.bookable_masters,
        )
        if assessed.is_visible:
            return format_html("{}<br><strong>Салон виден клиентам.</strong>", head)
        rows = format_html_join(
            "",
            "<li>{}</li>",
            ((REASON_LABELS[code],) for code in assessed.reasons),
        )
        return format_html(
            "{}<br><strong>Клиентам не виден. Причины:</strong><ul>{}</ul>",
            head,
            rows,
        )

    # ------------------------------------------------------------------
    # Экран подключения салона (DRF-1525)
    # ------------------------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "connect/",
                self.admin_site.admin_view(self.connect_view),
                name="tenancy_tenant_connect",
            ),
        ]
        return custom + super().get_urls()

    #: Имя поля кнопки верификации: в нём же приезжает id салона.
    #:
    #: DRF-1553. Отдельного URL у действия нет намеренно: оно живёт на
    #: экране подключения и нигде больше, а отдельный адрес пришлось бы
    #: защищать вторым разрешением — то самое «смешение двух прав»,
    #: которое владелец снял, оставив ОДИН уровень доступа (§51.1).
    VERIFY_FIELD = "verify_masters_of"

    def connect_view(self, request: HttpRequest) -> HttpResponse:
        """Форма «подключить салон» и действие верификации на ней же.

        Настройки тенанта меняет только суперпользователь (OPEN_DECISIONS
        §27 п.2, роли DRF-1495) — подключение тенанта тем более. Экран
        не дублирует ``create_tenant``: он вызывает :func:`connect_salon`,
        который проверяет идентификатор по Ayla ДО сохранения, запускает
        синхронизацию и показывает исход глазами клиента.

        DRF-1553 добавил сюда второе действие — верификацию мастеров.
        После DRF-1496 мастер, приехавший синхронизацией, рождается
        ``pending``, поэтому сразу после подключения бронируемых ноль и
        критерий успеха этого экрана («салон виден клиенту») без ручного
        шага не достигался НИКОГДА. Владелец выбрал дать шаг кнопкой, а
        не назвать его словами (§51.1), и уровень доступа оставил один —
        тот же суперпользователь; проверка прав здесь одна на оба
        действия, и кнопка ничьих прав не расширяет.

        Верификация идёт тем же сервисом, что действие ``verify_masters``
        в админке каталога (:func:`verify_salon_masters` →
        ``apps.catalog.services.verification``), поэтому и состояние, и
        строка журнала у двух экранов одинаковые.
        """
        if not request.user.is_superuser:
            raise PermissionDenied(
                "Подключение салона — действие владельца контура "
                "(суперпользователя), а не ролей админки."
            )

        verify_of = request.POST.get(self.VERIFY_FIELD) if request.method == "POST" else None

        form = SalonConnectForm(request.POST if verify_of is None else None)
        result = None
        error = None
        pending = None
        salon = None
        verified = None

        if verify_of is not None:
            try:
                salon = Tenant.all_objects.filter(pk=verify_of).first()
            except (ValidationError, ValueError):
                # Не UUID вовсе. ``UUIDField`` отвечает на это исключением,
                # а не пустой выборкой, и без перехвата экран отдал бы 500
                # на подделанной форме.
                salon = None
            if salon is None:
                # Не «верифицировано: 0», а отказ: салон, которого нет,
                # обязан выглядеть ошибкой, а не пустым успехом — тот же
                # класс тихого «успеха», ради которого заведён DRF-1525.
                error = (
                    f"Салон {verify_of!r} не найден — верификация не выполнена. "
                    "Подключите салон заново или верифицируйте мастеров "
                    "действием в админке каталога."
                )
            else:
                verified = verify_salon_masters(salon, user=request.user)
        elif request.method == "POST" and form.is_valid():
            try:
                result = connect_salon(
                    slug=form.cleaned_data["slug"],
                    name=form.cleaned_data["name"],
                    city=form.cleaned_data["city"],
                )
                salon = result.tenant
            except ConnectError as exc:
                error = str(exc)
            except ConnectPending as exc:
                # SETUP_PENDING, не отказ: данные верны, контур не
                # донастроен. Форма остаётся заполненной, чтобы после
                # настройки токена хватило одного нажатия.
                pending = exc

        # Оценка после действия, а не до: кнопка обещает «салон появится
        # в поиске», и экран обязан показать, случилось ли это на самом
        # деле, а не повторить обещание.
        if result is not None:
            # ``connect_salon`` уже посчитал исход — второй проход по тем
            # же таблицам дал бы то же самое за лишние запросы.
            assessment = result.assessment
        elif salon is not None:
            assessment = assess_salon(salon)
        else:
            assessment = None

        context = {
            **self.admin_site.each_context(request),
            "title": "Подключить салон",
            "form": form,
            "result": result,
            "salon": salon,
            "assessment": assessment,
            "assessment_reasons": (
                [REASON_LABELS[code] for code in assessment.reasons]
                if assessment is not None
                else []
            ),
            "verified": verified,
            "verify_field": self.VERIFY_FIELD,
            "error": error,
            "pending": pending,
            "opts": self.model._meta,  # noqa: SLF001 — admin chrome API
        }
        return TemplateResponse(request, "admin/tenancy/tenant/connect.html", context)

    # ------------------------------------------------------------------
    # Карточка подключённого салона: шаг верификации
    # ------------------------------------------------------------------

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Карточка салона плюс то же действие верификации, что на подключении.

        До этой правки кнопка жила ТОЛЬКО на экране подключения и
        рисовалась только при ``salon is not None`` — то есть ровно один
        раз, на странице итога сразу после нажатия «Подключить». На GET
        того же экрана ``salon`` пуст, а у салона, подключённого вчера,
        экрана итога нет вовсе: единственным путём к шагу оставалось
        знание про действие ``verify_masters`` в админке каталога. Шаг,
        без которого критерий успеха («салон виден клиенту») не
        достигается никогда (DRF-1496 → DRF-1553), не может быть виден
        один раз.

        Перехват идёт ДО ``super()``: POST карточки — это обычное
        сохранение формы тенанта, и без перехвата нажатие кнопки
        отправило бы форму, а не действие. Признак тот же, что на
        экране подключения — присутствие :data:`VERIFY_FIELD`.

        Уровень доступа не меняется: суперпользователь, как и на экране
        подключения (OPEN_DECISIONS §27 п.2, §51.1). Карточку тенанта
        открывает и роль с ``tenancy.change_tenant``; кнопки она не
        видит и нажать её не может — иначе один экран расширял бы
        права, которых другой не даёт.
        """
        if request.method == "POST" and self.VERIFY_FIELD in request.POST:
            return self._verify_from_card(request, object_id)

        salon = self.get_object(request, object_id)
        assessment = assess_salon(salon) if salon is not None else None
        extra_context = {
            **(extra_context or {}),
            "verify_field": self.VERIFY_FIELD,
            "salon_assessment": assessment,
            # Одно решение, а не два в шаблоне: «есть что предложить» и
            # «этому человеку можно» проверяются здесь, вместе, потому
            # что разъехаться им нельзя — кнопка, которую видно, но
            # нажать нельзя, обещает исход, которого не будет.
            "can_verify_masters": bool(
                assessment is not None
                and assessment.can_verify_masters
                and request.user.is_superuser
            ),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def _verify_from_card(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Нажатие кнопки на карточке — тем же сервисом, что везде.

        Ответ — редирект на ту же карточку, а не отрисовка на месте:
        обновление страницы после POST повторило бы верификацию, и
        оператор увидел бы «верифицировано: 0» там, где ничего не
        сломалось. Числа исхода уходят в штатное сообщение админки.
        """
        if not request.user.is_superuser:
            raise PermissionDenied(
                "Верификация мастеров — действие владельца контура "
                "(суперпользователя), а не ролей админки: тот же уровень "
                "доступа, что у экрана подключения салона."
            )
        salon = self.get_object(request, object_id)
        if salon is None:
            # Не «верифицировано: 0», а отказ: салон, которого нет,
            # обязан выглядеть ошибкой, а не пустым успехом.
            raise Http404("Салон не найден — верификация не выполнена.")

        outcome = verify_salon_masters(salon, user=request.user)
        message = (
            f"Верифицировано мастеров: {outcome.verified}. "
            f"Уже принятых пропущено: {outcome.skipped}."
        )
        if outcome.blocked:
            # DRF-1597: число печатается всегда, когда оно ненулевое —
            # «верифицировано: 0» без него читается как сбой.
            message += f" Не подтверждено — ждут нажатия самого мастера: {outcome.blocked}."
        self.message_user(request, message)
        return HttpResponseRedirect(request.path)

    def get_queryset(self, request):
        # Admin must see deactivated tenants too — use all_objects manager.
        return Tenant.all_objects.all()

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Штатной формы «Add Tenant» у салона нет — и не должно быть.

        Замер 09.09.2026, 05:57: владелец завёл ею салон
        ``testovuy-salin``. У строки ``last_catalog_sync_at = None``,
        такого UUID в Ayla нет, синхронизация не пыталась ни разу и не
        попытается. Это не ошибка оператора и не невезение: форма
        физически не может принять Ayla-UUID, потому что ``Tenant.id``
        объявлен ``editable=False`` — поле в форму не попадает, ключ
        подставляется случайный, а первичный ключ потом не меняется.
        Любой салон, рождённый здесь, нерабочий навсегда. Форма при
        этом молчит и рапортует успех.

        **Почему запрет, а не редирект на ``connect/``.** Дверей не
        две, а четырнадцать: кнопка в списке салонов плюс тринадцать
        зелёных плюсов «добавить» — по одному на каждую чужую форму
        админки с правимым выбором салона (``persona``,
        ``experiments``, ``catalog``, ``promptreg``, ``promotions``,
        ``scheduling``; посчитано по реестру ``admin.site``, сторож —
        ``test_no_green_plus_beside_tenant_selects``). Этот метод —
        единственный переключатель, который Django спрашивает про все
        четырнадцать сразу; редирект в ``add_view`` закрыл бы один
        адрес и оставил бы тринадцать плюсов, каждый из которых
        открывает всплывающее окно. Экран подключения всплывающих окон
        не знает: он не закроется, не подставит салон в поле-источник и
        не вернёт оператора туда, откуда тот пришёл. Мы бы поменяли
        одну тихую поломку на тринадцать новых.

        Здоровый путь никуда не делся и стоит на том же месте:
        «Подключить салон» в списке салонов (``connect/``) —
        единственный экран, который умеет спросить идентификатор,
        проверить его по Ayla ДО сохранения строки и показать исход.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        # When obj is None Django asks "may this user delete *anything* here?"
        # to decide whether to render the changelist's "Delete selected" action.
        # We return True so the dropdown still appears for regular tenants;
        # the per-row enforcement happens in delete_model / delete_queryset.
        if obj is not None and getattr(obj, "is_system", False):
            return False
        return super().has_delete_permission(request, obj)

    def delete_model(self, request, obj):
        if getattr(obj, "is_system", False):
            raise PermissionDenied(
                "System tenants cannot be deleted from admin. "
                "Clear `is_system` first or remove the row via shell with explicit intent."
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # Refuse the whole batch if any row is a system tenant — partial
        # deletes are worse than full refusal here (operator gets a clear
        # error instead of mixed success).
        if queryset.filter(is_system=True).exists():
            raise PermissionDenied(
                "Selection includes system tenants. Remove them from the "
                "selection or clear `is_system` first."
            )
        super().delete_queryset(request, queryset)
