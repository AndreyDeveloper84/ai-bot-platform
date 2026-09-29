"""Доступ смотрящего к данным клиента: пропуск и журнал (DRF-1514).

DRF-1495 завёл роль «смотрящий» с правом ``view_*`` на всё. Это значило:
любой заведённый человек открывал ``/admin/conversations/message/`` и
листал переписку клиентов **всех** салонов, не имея повода, и никто об
этом не узнавал — ``LogEntry`` пишется только на правку, а просмотр
правкой не является.

Решение владельца от 05.09.2026 закрывает это тремя вещами: списка
чужих переписок нет; переписка и профиль открываются только по
конкретному клиенту, найденному через обращение; у просмотра есть
причина и след.

Здесь живут две таблицы, и они разные по природе.

:class:`ClientDataAccessGrant` — **пропуск**. Живая, недолгая запись:
кто, к какому клиенту, по какому обращению и зачем открыл доступ.
Истекает по времени (:data:`~apps.adminconsole.client_access.GRANT_TTL`)
— пропуск, который не истекает, через неделю снова становится «правом
на всё».

:class:`ClientDataAccessLog` — **журнал**. Вечная, только-на-дозапись
лента: каждая выдача пропуска, каждый открытый экран и каждый отказ.

### Почему в журнале нет ни одного внешнего ключа

Журнал переживает то, о чём рассказывает. Учётную запись отзывают
(``admin_account_revoke``), клиента удаляют по запросу
(``delete_bot_user_data``), обращение может уехать в чистку. Строка
журнала обязана остаться читаемой после каждого из этих событий,
поэтому автор, клиент, салон и обращение записаны значениями, а не
ссылками. Это ровно та причина, по которой
``apps.adminconsole.journal`` кладёт в ``AuditLog`` и ``actor_pk``, и
``actor_username`` рядом.

### Чем это отличается от журнала изменений

``apps.adminconsole.journal`` (и экран ``/admin/admin/logentry/``)
отвечает на вопрос «что человек **изменил**». Эта таблица отвечает на
вопрос «что человек **увидел**». Ни одна из них не заменяет другую:
просмотр чужой переписки не меняет ни строки, поэтому в журнале
изменений его нет и быть не может.

### Телефона и медданных здесь нет

Ни в пропуске, ни в журнале не хранится ничего из содержимого:
``client_label`` — это ``display_name``/``client_name``/id канала
(``apps.adminconsole.client_access.client_label``), телефон в него не
попадает (DRF-1039), содержимое переписки и подавно.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def validate_reason(value: str) -> None:
    """Причина обязательна и что-то объясняет.

    Правило живёт в ``apps.adminconsole.client_access.check_reason`` —
    здесь только мостик к форме. Валидатор навешен на **поле модели**, а
    не на форму админки, по узкой причине: своя ``ModelForm`` собирает
    поля в момент импорта, и поле ``admin_task`` при этом дёргает
    ``AdminTask._default_manager`` — тенант-скоупный менеджер без
    тенанта в контексте. Валидатор на поле даёт ту же проверку в форме,
    и вдобавок работает из кода и команд, а не только с экрана.

    Импорт внутри функции разрывает цикл: ``client_access`` импортирует
    модели.
    """
    from apps.adminconsole.client_access import ClientAccessError, check_reason

    try:
        check_reason(value)
    except ClientAccessError as exc:
        raise ValidationError(str(exc)) from exc


class ClientDataAccessGrant(models.Model):
    """Пропуск к данным одного клиента по одному обращению."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        editable=False,
        verbose_name="Кто открыл",
        help_text="Пропуск живёт часами; удаление учётной записи "
        "гасит его вместе с ней. След остаётся в журнале доступа.",
    )
    actor_username = models.CharField(
        max_length=150,
        blank=True,
        editable=False,
        verbose_name="Имя автора",
    )
    admin_task = models.ForeignKey(
        "handoff.AdminTask",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="Обращение",
        help_text="Задача из очереди handoff, с которой вы работаете. "
        "Клиент и салон берутся из неё — вручную их выбрать нельзя.",
    )
    client_id = models.UUIDField(
        editable=False,
        verbose_name="Клиент",
        help_text="identity.BotUser.id — берётся из обращения.",
    )
    client_label = models.CharField(
        max_length=200,
        blank=True,
        editable=False,
        verbose_name="Клиент (как показывать)",
    )
    tenant_slug = models.CharField(
        max_length=100,
        blank=True,
        editable=False,
        verbose_name="Салон",
    )
    reason = models.TextField(
        validators=[validate_reason],
        verbose_name="Причина просмотра",
        help_text="Зачем открываете переписку и профиль. Причина "
        "попадает в журнал доступа и читается человеком, а не машиной.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, editable=False)
    expires_at = models.DateTimeField(editable=False, verbose_name="Действует до")

    objects = models.Manager()

    class Meta:
        verbose_name = "Доступ к данным клиента"
        verbose_name_plural = "Доступ к данным клиента"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["actor", "expires_at"], name="acc_grant_actor_exp_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.actor_username} → {self.client_label or self.client_id}"


class ClientDataAccessLog(models.Model):
    """Журнал доступа: кто, когда, кого смотрел и с какой причиной.

    Только на дозапись. Правка и удаление закрыты и правами
    (``adminconsole`` в ``EDITOR_DENIED_APP_LABELS``), и экраном
    (:class:`~apps.adminconsole.admin.ClientDataAccessLogAdmin`).
    """

    class Outcome(models.TextChoices):
        GRANTED = "granted", "доступ открыт"
        OPENED = "opened", "экран открыт"
        DENIED = "denied", "отказано"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, db_index=True)
    actor_username = models.CharField(max_length=150, blank=True)
    actor_pk = models.CharField(
        max_length=64,
        blank=True,
        help_text="Первичный ключ учётной записи строкой — ссылки нет "
        "намеренно, запись переживает отзыв учётки.",
    )
    screen = models.CharField(
        max_length=120,
        blank=True,
        help_text="`<app_label>.<model>` открытого экрана. Пусто — когда "
        "строка про выдачу пропуска, а не про открытый экран.",
    )
    object_id = models.CharField(max_length=200, blank=True)
    client_id = models.UUIDField(null=True, blank=True)
    client_label = models.CharField(max_length=200, blank=True)
    tenant_slug = models.CharField(max_length=100, blank=True)
    admin_task_id = models.UUIDField(null=True, blank=True)
    grant_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True)
    detail = models.CharField(
        max_length=300,
        blank=True,
        help_text="Почему отказано — человеческим языком, тот же текст, что увидел отказанный.",
    )

    objects = models.Manager()

    class Meta:
        verbose_name = "Журнал доступа к данным клиента"
        verbose_name_plural = "Журнал доступа к данным клиента"
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["client_id", "occurred_at"], name="acc_log_client_at_idx"),
            models.Index(fields=["actor_username", "occurred_at"], name="acc_log_actor_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.actor_username} {self.outcome}"
