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
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from apps.adminconsole.theme import AylaAdminMedia
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff
from apps.tenancy.onboarding import (
    REASON_LABELS,
    ConnectError,
    ConnectPending,
    assess_salon,
    connect_preflight,
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
        # Решение владельца, дословно: «нельзя случайно активировать
        # operator-действием». До этой строки галочка «активен» правилась
        # прямо из карточки — то есть салон включался мимо всяких
        # проверок, одним движением и без следа о том, кто это сделал.
        #
        # Возможность не отнимается насовсем: явная реактивация уезжает в
        # доменную операцию, где у неё будут проверки и авторство.
        # Командный путь остаётся и сейчас. Здесь закрывается СЛУЧАЙНОЕ
        # действие, а не намеренное.
        "is_active",
        "is_system",
        "created_at",
        "updated_at",
        "telegram_bot_token_state",
        "telegram_webhook_secret_state",
        "salon_visibility_state",
        "inactive_consequences",
        "last_catalog_sync_at",
        "last_catalog_sync_ok_at",
    )
    fieldsets = (
        (
            "Салон",
            {
                "fields": (
                    "id",
                    "slug",
                    "name",
                    "city",
                    "address",
                    "is_active",
                    "inactive_consequences",
                    "is_system",
                ),
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

        # DRF-1613: невозможность подключения называется ДО формы, по факту
        # из настроек, а не после нажатия. Верификацию (``verify_of``) это
        # не гейтит — токен ей не нужен.
        preflight = connect_preflight()

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
        elif request.method == "POST" and not preflight.ok:
            # Кнопки на экране нет, но POST руками возможен: тот же факт,
            # тот же текст, в каталог не ходим, строки нет.
            pass
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
            "preflight": preflight,
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

    @admin.display(description="Что запрещает неактивность")
    def inactive_consequences(self, obj: Tenant) -> str:
        """Последствия неактивности — словами, а не галочкой.

        Галочка ``is_active`` уже стояла в карточке, и оператору она не
        говорила ни одного из четырёх последствий. «Флаг показан» и
        «человек знает, что произойдёт» — разные вещи, и дорогая здесь
        вторая: по ней решают, чинить салон или заводить заново.

        Три ответа, а не два, по образцу соседних колонок этой же
        карточки: у новой строки (салон ещё не сохранён) ответа нет
        вовсе, и подставлять ей «всё разрешено» значило бы утверждать
        измеренное там, где ничего не измеряли.

        «Не сохранена» определяется по ``_state.adding``, а НЕ по
        ``pk is None``: ``Tenant.id`` объявлен с ``default=uuid.uuid4``,
        поэтому ключ есть у строки уже в момент создания объекта в
        памяти. Проверка по ``pk`` здесь никогда не сработала бы — и
        выглядела бы при этом совершенно рабочей.
        """
        if obj._state.adding:
            return "нет данных"
        if obj.is_active:
            return "Салон активен — ограничений по этой причине нет."
        return format_html(
            "<b>Салон неактивен.</b> Пока это так:<ul>{}</ul>"
            "<i>Снять неактивность галочкой нельзя — это отдельная "
            "операция с проверками.</i>",
            format_html_join(
                "",
                "<li>{}</li>",
                (
                    (item,)
                    for item in (
                        "мастера нельзя сделать публичным;",
                        "новую запись начать нельзя;",
                        "привязка локации или услуги не снимает запрет на публикацию;",
                        "случайно активировать салон действием оператора нельзя.",
                    )
                ),
            ),
        )

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


# ---------------------------------------------------------------------------
# Platform Operations — доступы сотрудников и приглашения, ТОЛЬКО ЧТЕНИЕ.
#
# До этих карточек оператор не видел в админке ни одного ответа на вопрос
# «кто в этом салоне админ» — модели есть с ADR-0008, карточек нет ни у
# одной. Смотреть приходилось в базу.
#
# Почему только чтение, и почему это не временная мера. Выдача и отзыв
# доступа — операции доменного слоя: у них свои проверки, свой аудит и
# своё «кем выдан». Admin-форма, которая пишет в эти таблицы напрямую,
# воспроизвела бы бизнес-логику мимо них — именно то, что запрещено
# первым пунктом задачи. Поэтому здесь показ, а мутации придут ручкой
# домена, когда её контракт будет объявлен.
#
# Идиома запрета взята дословно у зеркала каталога
# (``apps/catalog/admin.py``::``_MirrorAdminBase``): три ``has_*_permission``
# возвращают ``False`` безусловно, а не «False, если не суперпользователь» —
# иначе запрет держался бы на роли читателя, а не на природе таблицы.
# ---------------------------------------------------------------------------


def _looks_like_uuid(value: str) -> bool:
    """Отсев до запроса: кривой id — «не найден», а не 500 из ``UUIDField``."""
    import uuid

    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class _ReadOnlyStaffAdmin(AylaAdminMedia, admin.ModelAdmin):
    """Общая часть обеих карточек: показывать, но не трогать.

    Видимость закрыта отдельным правом, и это решение владельца:
    ``is_staff`` означает ровно «может войти в Django Admin» и бизнес-правом
    не является. Здесь показ идёт ПОПЕРЁК САЛОНОВ (``get_queryset`` берёт
    ``all_tenants``), то есть это ровно тот cross-tenant доступ, ради
    которого право и заведено. Без этой проверки разделение существовало бы
    на словах: любой, кто попал в админку, видел бы доступы всех салонов.
    """

    #: Право оператора платформы. Строкой, а не импортом: Django сверяет
    #: право по имени, и имя обязано совпадать с ``Meta.permissions``.
    PLATFORM_OPERATIONS_PERM = "tenancy.platform_operations"

    #: Как выглядит отсутствие значения. Не прочерк и не ноль: прочерк в
    #: колонке читается как ноль, а ноль — как измеренное значение.
    empty_value_display = "нет данных"

    def has_view_permission(self, request: HttpRequest, obj=None) -> bool:
        return request.user.has_perm(self.PLATFORM_OPERATIONS_PERM)

    def has_manage_permission(self, request: HttpRequest) -> bool:
        """DRF-2082: действия над доступами — тем же правом, что и просмотр.

        ``has_change_permission`` здесь безусловно ``False`` (форма не
        пишет), поэтому у действий свой предикат ``permissions=["manage"]``,
        как у ``has_onboard_permission`` в карточке мастера (#1811). Без
        права действий нет в списке — не только отказ при нажатии.
        """
        return request.user.has_perm(self.PLATFORM_OPERATIONS_PERM)

    def has_module_permission(self, request: HttpRequest) -> bool:
        # Иначе карточка светится в индексе приложения тому, кто открыть
        # её всё равно не сможет: список имён — тоже сведения.
        return request.user.has_perm(self.PLATFORM_OPERATIONS_PERM)

    def get_queryset(self, request: HttpRequest):
        # ``all_tenants`` — как у зеркала: админка показывает все салоны,
        # а не только тот, в контексте которого пришёл запрос.
        return self.model.all_tenants.all()

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(TenantStaff)
class TenantStaffAdmin(_ReadOnlyStaffAdmin):
    """Кто и с какой ролью имеет доступ к салону (ADR-0008).

    Роли здесь ровно три — ``receptionist``, ``admin``, ``owner``. Мастера
    в этой таблице НЕ живут: мастер — это связь
    ``CatalogMaster.linked_bot_user``, а не строка доступа. Колонка
    «Мастер» отсутствует намеренно, чтобы карточка не утверждала
    членства, которого в модели нет.
    """

    list_display = ("tenant", "bot_user", "role", "access_state", "created_at", "created_by")
    list_filter = ("role", "tenant")
    search_fields = ("tenant__name", "tenant__slug")
    search_help_text = "Ищет по названию салона и его коду."
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    # Поля перечислены поимённо, а не оставлены на усмотрение Django:
    # список по умолчанию берёт все редактируемые поля, и новое поле
    # модели появилось бы на экране само, никем не решённое.
    fields = ("tenant", "bot_user", "role", "created_at", "created_by", "deactivated_at")
    readonly_fields = fields
    change_list_template = "admin/tenancy/tenantstaff/change_list.html"
    actions = ("change_staff_role", "revoke_staff_access")

    #: Сколько людей показывать на странице «выдать роль» — недавно активные
    #: строки салона; для остальных есть поле с id (как у карточки мастера).
    GRANT_CANDIDATE_LIMIT = 200

    #: Слово оператору на каждый именованный исход сервисов. Ключи — слуги
    #: исключений и ответов ядра; полноту держит тест.
    ROLE_WORDS: dict[str, str] = {
        "already_had_role": "У этого человека уже есть такая роль в салоне — второй строки нет.",
        "owner_already_exists": (
            "В салоне уже есть действующий владелец. Передача владения — "
            "сначала отозвать прежнего, потом выдать роль."
        ),
        "owner_role_locked": (
            "Роль действующего владельца этим действием не меняется. Передача "
            "владения — отозвать доступ, потом выдать роль новому."
        ),
        "owner_revoke_refused": (
            "Доступ действующего владельца здесь не отзывается — это отдельное "
            "решение, не операторское действие."
        ),
        "no_active_role": "У этого человека нет действующей роли в салоне — менять нечего.",
        "unknown_role": "Такой роли в словаре нет.",
        "person_in_other_tenant": "Этот человек принадлежит другому салону.",
        "foreign_tenant": "Оператор не может действовать в этом салоне.",
    }

    @admin.display(description="Состояние доступа")
    def access_state(self, obj: TenantStaff) -> str:
        """Действует доступ или отозван — словами, а не датой.

        ``deactivated_at`` пустой читается оператором как «поле не
        заполнили», а не как «доступ действует». Разница дорогая: по ней
        решают, есть ли у человека права прямо сейчас.
        """
        if obj.deactivated_at is None:
            return "Действует"
        return f"Отозван {obj.deactivated_at:%d.%m.%Y}"

    # --- DRF-2082: управление доступами — через сервисы, не мимо ------------

    def _actor(self, request: HttpRequest):  # noqa: ANN202
        from apps.identity.services.specialist_onboarding import OnboardingActor

        return OnboardingActor(
            surface="django_admin",
            audit_label=f"django_admin:user={request.user.pk}",
            cross_tenant=True,
            capability="platform_operations",
        )

    def _refusal(self, request: HttpRequest, slug: str) -> None:
        self.message_user(request, self.ROLE_WORDS.get(slug, f"Отказ: {slug}."), messages.ERROR)

    def get_urls(self):  # type: ignore[no-untyped-def]
        return [
            path(
                "grant/",
                self.admin_site.admin_view(self.grant_view),
                name="tenancy_tenantstaff_grant",
            ),
            *super().get_urls(),
        ]

    def grant_view(self, request: HttpRequest) -> HttpResponse:
        """«Выдать роль»: салон → человек из этого салона → роль → сервис.

        Пишет ``grant_role_by_operator`` (то же ядро, что у кода приглашения);
        страница только выбирает. Повтор — «уже есть» (предупреждение, не
        ошибка и не вторая строка); чужой салон в id — отказ по имени.
        """
        from apps.identity.models import BotUser
        from apps.identity.services import staff_roles
        from apps.identity.services.staff_invites import OwnerAlreadyExists

        if not self.has_manage_permission(request):
            raise PermissionDenied
        opts = self.model._meta  # noqa: SLF001
        tenants = list(Tenant.all_objects.order_by("name"))
        raw_tenant = (request.POST.get("tenant") or request.GET.get("tenant") or "").strip()
        tenant = (
            Tenant.all_objects.filter(pk=raw_tenant).first()
            if _looks_like_uuid(raw_tenant)
            else None
        )

        if request.method == "POST" and request.POST.get("apply") and tenant is not None:
            raw_id = (
                request.POST.get("bot_user_id_manual") or request.POST.get("bot_user_id") or ""
            ).strip()
            person = (
                BotUser.all_tenants.filter(
                    pk=raw_id, tenant_id=tenant.id, deleted_at__isnull=True
                ).first()
                if _looks_like_uuid(raw_id)
                else None
            )
            role = (request.POST.get("role") or "").strip()
            if person is None:
                self._refusal(request, "person_in_other_tenant")
            else:
                try:
                    result = staff_roles.grant_role_by_operator(
                        tenant=tenant, bot_user=person, role=role, actor=self._actor(request)
                    )
                except staff_roles.StaffRoleError as exc:
                    self._refusal(request, exc.slug)
                except OwnerAlreadyExists:
                    self._refusal(request, "owner_already_exists")
                else:
                    if result.already_had_role:
                        self.message_user(
                            request, self.ROLE_WORDS["already_had_role"], messages.WARNING
                        )
                    else:
                        self.log_change(
                            request, tenant, f"Выдана роль {role} человеку {person.pk} (DRF-2082)."
                        )
                        self.message_user(
                            request,
                            f"Роль {role} выдана. Доступ действует сразу.",
                            messages.SUCCESS,
                        )
                    return HttpResponseRedirect(reverse("admin:tenancy_tenantstaff_changelist"))
            return HttpResponseRedirect(f"{request.path}?tenant={tenant.pk}")

        candidates = (
            BotUser.all_tenants.filter(tenant_id=tenant.id, deleted_at__isnull=True)
            .only("id", "display_name", "channel", "last_seen")
            .order_by("-last_seen")[: self.GRANT_CANDIDATE_LIMIT]
            if tenant is not None
            else []
        )
        return TemplateResponse(
            request,
            "admin/tenancy/tenantstaff/grant.html",
            {
                **self.admin_site.each_context(request),
                "title": "Выдать роль в салоне",
                "opts": opts,
                "tenants": tenants,
                "tenant": tenant,
                "candidates": candidates,
                "candidate_limit": self.GRANT_CANDIDATE_LIMIT,
                "roles": TenantStaff.Role.choices,
            },
        )

    @admin.action(permissions=["manage"], description="Сменить роль (одна строка)")
    def change_staff_role(self, request: HttpRequest, queryset):  # type: ignore[no-untyped-def]
        """Сервис ``change_staff_role``: старая строка закрывается, новая выдаётся."""
        from apps.identity.services import staff_roles
        from apps.identity.services.staff_invites import OwnerAlreadyExists

        if queryset.count() != 1:
            self.message_user(request, "Сменить роль можно одной строке за раз.", messages.ERROR)
            return None
        row = queryset.select_related("tenant", "bot_user").first()
        if request.POST.get("apply"):
            role = (request.POST.get("role") or "").strip()
            try:
                change = staff_roles.change_staff_role(
                    tenant=row.tenant, bot_user=row.bot_user, role=role, actor=self._actor(request)
                )
            except staff_roles.StaffRoleError as exc:
                self._refusal(request, exc.slug)
            except OwnerAlreadyExists:
                self._refusal(request, "owner_already_exists")
            else:
                self.log_change(
                    request,
                    row,
                    f"Роль изменена: {', '.join(change.previous_roles)} → {change.role} (DRF-2082).",
                )
                self.message_user(
                    request,
                    f"Роль изменена: {', '.join(change.previous_roles)} → {change.role}.",
                    messages.SUCCESS,
                )
            return None
        return TemplateResponse(
            request,
            "admin/tenancy/tenantstaff/change_role.html",
            {
                **self.admin_site.each_context(request),
                "title": "Сменить роль",
                "opts": self.model._meta,  # noqa: SLF001
                "row": row,
                "roles": TenantStaff.Role.choices,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
            },
        )

    @admin.action(permissions=["manage"], description="Отозвать доступ (с причиной)")
    def revoke_staff_access(self, request: HttpRequest, queryset):  # type: ignore[no-untyped-def]
        """Сервис ``revoke_staff_access``: fail-closed, ``deactivated_at``, владелец — отказ."""
        from apps.identity.services.staff_revoke import OwnerRevokeRefused, revoke_staff_access

        if queryset.count() != 1:
            self.message_user(request, "Отозвать доступ можно одной строке за раз.", messages.ERROR)
            return None
        row = queryset.select_related("tenant", "bot_user").first()
        if request.POST.get("apply"):
            reason = (request.POST.get("reason") or "").strip()
            if not reason:
                self.message_user(
                    request, "Отзыв без причины не допускается — укажите причину.", messages.ERROR
                )
                return None
            actor = self._actor(request)
            try:
                result = revoke_staff_access(
                    tenant=row.tenant,
                    bot_user=row.bot_user,
                    reason=reason,
                    surface=actor.surface,
                    actor_label=actor.audit_label,
                )
            except OwnerRevokeRefused:
                self._refusal(request, "owner_revoke_refused")
                return None
            self.log_change(request, row, f"Доступ отозван. Причина: {reason} (DRF-2082).")
            if result.changed:
                text = f"Доступ отозван: {', '.join(result.roles_revoked) or '—'}"
                if result.master_unlinked:
                    text += ", связь мастера снята"
                text += ". Mini App салона закрыт сразу."
                self.message_user(request, text, messages.SUCCESS)
            else:
                self.message_user(
                    request, "Доступа уже не было — ничего не изменилось.", messages.WARNING
                )
            return None
        return TemplateResponse(
            request,
            "admin/tenancy/tenantstaff/revoke.html",
            {
                **self.admin_site.each_context(request),
                "title": "Отозвать доступ",
                "opts": self.model._meta,  # noqa: SLF001
                "row": row,
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
            },
        )


@admin.register(StaffInvite)
class StaffInviteAdmin(_ReadOnlyStaffAdmin):
    """Приглашения сотрудников: кого позвали, кем и чем это кончилось.

    ВНИМАНИЕ: в боте слово «приглашение» означает ДВА разных предмета, и
    путать их дорого.

    * **Здесь** — ``StaffInvite`` (DRF-1061): приглашение КОДОМ, у него
      ``code_hash`` и срок. Им зовут человека стать сотрудником салона.
    * **В карточке мастера** (``apps/catalog/admin.py``) — совсем другое:
      ``CatalogMaster.invite_status`` / ``invite_token``, приглашение
      МАСТЕРА, приезжающее синхронизацией и отзываемое действием
      ``revoke_invite_masters``.

    У обеих карточек колонка называется «состояние приглашения», и это
    единственное, что у них общего. Отзыв одного не отзывает другое.

    **Отозвать выписанный код нечем — и это состояние системы, а не
    ограничение этой карточки.** Замер ayla-5f: функций ``revoke``/
    ``cancel`` в ``apps/identity/services/staff_invites.py`` — ноль из
    пятнадцати, при положительном контроле той же командой по
    ``staff_revoke.py`` (1 из 1). По всему дереву ``StaffInvite`` не
    гасит никто: ни админка, ни API, ни сервисный слой. Гашение только
    пассивное — срок (``expires_at``) и однократность (``used_at``).
    Поэтому здесь нет действия отзыва: звать нечего, а кнопка,
    показывающая несуществующую возможность, хуже её отсутствия.

    **А отмена МАСТЕРСКОГО приглашения бывает трёх происхождений**, и
    соседняя колонка их не различает: действие оператора в каталоге
    (``catalog/admin.py``), отказ самого мастера
    (``master_api/views.py``) и склейка DRF-1507, где строка замещается
    другой — единственный случай, когда гасится и токен. На экране все
    три выглядят одинаково «отменено».

    ``code_hash`` не показывается НИГДЕ — ни в списке, ни в карточке.
    Это дайджест кода приглашения, то есть учётные данные; в админке им
    не место даже на чтение. Поля перечислены поимённо именно поэтому:
    умолчание Django показало бы всё редактируемое, включая его.

    Роли здесь ЧЕТЫРЕ, в отличие от таблицы доступов: добавлен
    ``master``. Приглашение мастера — законный случай, а строки доступа
    у мастера не возникает: принятое приглашение связывает
    ``CatalogMaster``, а не заводит ``TenantStaff``.
    """

    list_display = ("tenant", "role", "catalog_master", "invite_state", "expires_at", "created_by")
    list_filter = ("role", "tenant")
    search_fields = ("tenant__name", "tenant__slug", "note")
    search_help_text = "Ищет по названию салона, его коду и заметке."
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    #: Поля карточки одним кортежем: на него ссылаются и ``fieldsets``, и
    #: ``readonly_fields``. Django не разрешает объявить ``fields`` и
    #: ``fieldsets`` разом, а два перечня разошлись бы молча.
    _INVITE_FIELDS = (
        "tenant",
        "role",
        "catalog_master",
        "expires_at",
        "used_at",
        "used_by",
        "revoked_at",
        "created_at",
        "created_by",
        "note",
    )
    change_list_template = "admin/tenancy/staffinvite/change_list.html"
    actions = ("revoke_staff_invite",)

    #: Слово оператору на каждый именованный исход.
    CODE_WORDS: dict[str, str] = {
        "invite_already_used": (
            "Код уже использован — отзывать нечего. Выданный им доступ снимается "
            "действием «Отозвать доступ» на карточке доступов."
        ),
        "master_required": "Для роли «master» нужно выбрать мастера этого салона.",
        "master_not_in_tenant": "Этот мастер принадлежит другому салону.",
        "master_only_for_master_role": "Мастер указывается только для роли «master».",
        "tenant_required": "Выберите салон.",
        "unknown_role": "Такой роли в словаре нет.",
    }
    fieldsets = (
        (
            "Приглашение",
            {
                "fields": _INVITE_FIELDS,
                "description": (
                    "Отозвать выписанный код — действие «Отозвать код» в списке "
                    "приглашений (DRF-2082): ставит <i>Отозвано</i>, и ввод кода "
                    "после этого отклоняется. Использованный код не отзывается — "
                    "выданный им доступ снимается действием «Отозвать доступ» на "
                    "карточке доступов. Пассивное гашение остаётся: срок "
                    "(<i>Действует до</i>) и однократность (<i>Использовано</i>). "
                    "Сам код здесь не показывается никогда: он был показан один раз "
                    "при выдаче, в базе только хеш."
                ),
            },
        ),
    )
    readonly_fields = _INVITE_FIELDS

    @admin.display(description="Состояние приглашения")
    def invite_state(self, obj: StaffInvite) -> str:
        """Три ответа, а не два.

        «Не использовано» и «срок истёк» — разные состояния, и слить их
        значило бы спрятать ровно тот случай, ради которого оператор сюда
        пришёл: приглашение, которое человек уже не сможет принять.
        Порядок проверок тоже не косметика — использованное приглашение
        остаётся использованным после истечения срока.
        """
        if obj.used_at is not None:
            return f"Использовано {obj.used_at:%d.%m.%Y}"
        if obj.revoked_at is not None:
            # DRF-2082: четвёртое состояние — активный отзыв оператором. Стоит
            # ПОСЛЕ «использовано» (использованный не отзывается) и ДО «истёк»
            # (отозванный истекать уже нечему).
            return f"Отозвано {obj.revoked_at:%d.%m.%Y}"
        if obj.expires_at is not None and obj.expires_at <= timezone.now():
            return f"Срок истёк {obj.expires_at:%d.%m.%Y}"
        return "Ждёт принятия"

    # --- DRF-2082 (PR-2): выдать / отозвать код — через сервисы, не мимо ------

    def _actor(self, request: HttpRequest):  # noqa: ANN202
        from apps.identity.services.specialist_onboarding import OnboardingActor

        return OnboardingActor(
            surface="django_admin",
            audit_label=f"django_admin:user={request.user.pk}",
            cross_tenant=True,
            capability="platform_operations",
        )

    def get_urls(self):  # type: ignore[no-untyped-def]
        return [
            path(
                "issue/",
                self.admin_site.admin_view(self.issue_view),
                name="tenancy_staffinvite_issue",
            ),
            *super().get_urls(),
        ]

    def issue_view(self, request: HttpRequest) -> HttpResponse:
        """«Выдать код»: тот же путь, что `issue_staff_invite` с хоста.

        Код показывается оператору ОДИН РАЗ — на странице результата, которая
        является ответом на POST; в базе только хеш, GET этого адреса кода не
        несёт по построению. В `LogEntry` и аудит — id приглашения, роль,
        срок; ни код, ни хеш.
        """
        from apps.audit.services import write_audit
        from apps.catalog.models import CatalogMaster
        from apps.events.vocabulary import STAFF_INVITE_ISSUED
        from apps.identity.services.staff_invites import INVITE_TTL_DAYS, issue_staff_invite
        from apps.tenancy.context import tenant_scope

        if not self.has_manage_permission(request):
            raise PermissionDenied
        tenants = list(Tenant.all_objects.order_by("name"))
        raw_tenant = (request.POST.get("tenant") or request.GET.get("tenant") or "").strip()
        tenant = (
            Tenant.all_objects.filter(pk=raw_tenant).first()
            if _looks_like_uuid(raw_tenant)
            else None
        )
        # Чтение мастеров — под скоупом выбранного салона (менеджер по умолчанию
        # фильтрует по tenant), а не через all_tenants: cross-tenant чтение
        # каталога вне apps/marketplace запрещено сторожем MKT1.
        masters: list[CatalogMaster] = []
        if tenant is not None:
            with tenant_scope(tenant):
                masters = list(
                    CatalogMaster.objects.filter(archived_at__isnull=True)
                    .only("id", "name", "linked_bot_user")
                    .order_by("name")
                )
        context = {
            **self.admin_site.each_context(request),
            "title": "Выдать код приглашения",
            "opts": self.model._meta,  # noqa: SLF001
            "tenants": tenants,
            "tenant": tenant,
            "masters": masters,
            "roles": StaffInvite.Role.choices,
            "ttl_days": INVITE_TTL_DAYS,
        }

        if request.method == "POST" and request.POST.get("apply"):
            role = (request.POST.get("role") or "").strip()
            note = (request.POST.get("note") or "").strip()[:200]
            raw_master = (request.POST.get("catalog_master") or "").strip()
            slug = None
            master = None
            if tenant is None:
                slug = "tenant_required"
            elif role not in StaffInvite.Role.values:
                slug = "unknown_role"
            elif role == StaffInvite.Role.MASTER:
                master = None
                if _looks_like_uuid(raw_master):
                    with tenant_scope(tenant):
                        master = CatalogMaster.objects.filter(pk=raw_master).first()
                if not raw_master:
                    slug = "master_required"
                elif master is None:
                    slug = "master_not_in_tenant"
            elif raw_master:
                slug = "master_only_for_master_role"
            if slug is not None:
                self.message_user(request, self.CODE_WORDS[slug], messages.ERROR)
                return HttpResponseRedirect(
                    f"{request.path}?tenant={tenant.pk}" if tenant is not None else request.path
                )

            invite, code = issue_staff_invite(
                tenant=tenant, role=role, catalog_master=master, created_by=None, note=note
            )
            actor = self._actor(request)
            with tenant_scope(tenant):
                write_audit(
                    STAFF_INVITE_ISSUED,
                    target="tenancy.StaffInvite",
                    target_id=invite.pk,
                    payload={
                        "surface": actor.surface,
                        "actor_label": actor.audit_label,
                        "role": invite.role,
                        "master_id": str(master.pk) if master is not None else None,
                        "expires_at": invite.expires_at.isoformat(),
                    },
                )
            # Ни кода, ни хеша: журнал — место, куда смотрят.
            self.log_addition(
                request, invite, f"Выписан код приглашения (роль {invite.role}, DRF-2082)."
            )
            return TemplateResponse(
                request,
                "admin/tenancy/staffinvite/issued.html",
                {
                    **context,
                    "title": "Код приглашения выписан",
                    "invite": invite,
                    "code": code,
                    "master": master,
                },
            )

        return TemplateResponse(request, "admin/tenancy/staffinvite/issue.html", context)

    @admin.action(permissions=["manage"], description="Отозвать код (с причиной)")
    def revoke_staff_invite(self, request: HttpRequest, queryset):  # type: ignore[no-untyped-def]
        """Сервис `revoke_staff_invite` на каждую выбранную строку; исходы — по имени."""
        from apps.identity.services.staff_invites import InviteAlreadyUsed, revoke_staff_invite

        if not request.POST.get("apply"):
            return TemplateResponse(
                request,
                "admin/tenancy/staffinvite/revoke.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Отозвать код приглашения",
                    "opts": self.model._meta,  # noqa: SLF001
                    "rows": list(queryset.select_related("tenant")),
                    "action_checkbox_name": ACTION_CHECKBOX_NAME,
                },
            )
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            self.message_user(
                request, "Отзыв без причины не допускается — укажите причину.", messages.ERROR
            )
            return None
        actor = self._actor(request)
        revoked = already = 0
        for invite in queryset:
            try:
                result = revoke_staff_invite(
                    invite, surface=actor.surface, actor_label=actor.audit_label, reason=reason
                )
            except InviteAlreadyUsed as exc:
                self.message_user(request, self.CODE_WORDS[exc.slug], messages.ERROR)
                continue
            if result.changed:
                revoked += 1
                self.log_change(request, invite, f"Код отозван. Причина: {reason} (DRF-2082).")
            else:
                already += 1
        if revoked or already:
            self.message_user(
                request,
                f"Отозвано: {revoked}. Уже были отозваны: {already}. Ввод этих кодов отклоняется.",
                messages.SUCCESS if revoked else messages.WARNING,
            )
        return None
