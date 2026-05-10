"""Централизованные уведомления о заявках — Telegram + email.

Независимое Python-приложение. Раньше жило в `website/notifications.py`,
что создавало циклические зависимости:
  - payments → website.notifications → services_app → website
  - services_app/admin (lazy) → website.notifications → services_app

Вынос в отдельный пакет `notifications/` разрывает эти циклы: notifications
теперь зависит только от services_app (для чтения SiteSettings, lazy import)
и не требует ничего от website/payments.

Вызывается из всех endpoint'ов и задач, где создаётся заявка/заказ:
api_wizard_booking, api_bundle_request, api_certificate_request,
payments.views.yookassa_webhook, payments.tasks.fulfill_*.

- Telegram: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID из окружения
- Email:    список получателей из SiteSettings.notification_emails
            (редактируется в админке), fallback на ADMIN_NOTIFICATION_EMAIL
"""
import logging

import requests as http_requests
from django.conf import settings as django_settings
from django.core.mail import EmailMessage, send_mail

logger = logging.getLogger(__name__)


def send_notification_telegram(text: str) -> bool:
    """Отправляет текст в Telegram чат администратора.

    api.telegram.org заблокирован в РФ — используем HTTP-прокси из
    TELEGRAM_PROXY (приоритет) или OPENAI_PROXY (fallback) если задано.
    Без прокси на проде в РФ запрос упадёт с Network unreachable.

    Ничего не делает, если TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы
    или если POST упал (ошибка логируется, исключение не пробрасывается).
    Возвращает True при успешной отправке (HTTP 2xx).
    """
    token = getattr(django_settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(django_settings, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    proxy_url = (
        getattr(django_settings, "TELEGRAM_PROXY", "")
        or getattr(django_settings, "OPENAI_PROXY", "")
    )
    proxies = {"https": proxy_url} if proxy_url else None

    try:
        r = http_requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
            proxies=proxies,
        )
        if not r.ok:
            logger.warning(
                "Telegram notification HTTP %s: %s", r.status_code, r.text[:200]
            )
        return r.ok
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")
        return False


def get_notification_recipients() -> list[str]:
    """Список email-адресов для уведомлений о заявках.

    Приоритет — SiteSettings.notification_emails (редактируется в админке),
    fallback на ADMIN_NOTIFICATION_EMAIL из окружения.

    Lazy import SiteSettings намеренно: notifications не должен зависеть от
    services_app на уровне импорта модуля — это бы создало circular dep при
    load-order apps.
    """
    from services_app.models import SiteSettings

    site = SiteSettings.objects.first()
    if site:
        recipients = site.get_notification_emails()
        if recipients:
            return recipients

    fallback = getattr(django_settings, "ADMIN_NOTIFICATION_EMAIL", "")
    return [fallback] if fallback else []


def send_certificate_email(order, cert, pdf_bytes: bytes | None = None) -> bool:
    """Отправляет покупателю email с кодом сертификата после оплаты.

    Если передан pdf_bytes — прикрепляет PDF-файл сертификата.
    """
    if not order.client_email:
        return False

    if cert.certificate_type == "nominal":
        value_str = f"{cert.nominal:,.0f} ₽".replace(",", " ")
    elif cert.certificate_type == "bundle" and cert.bundle:
        value_str = cert.bundle.name
    else:
        value_str = str(cert.service) if cert.service else "—"

    recipient_line = (
        f"Получатель: {cert.recipient_name}\n" if cert.recipient_name else ""
    )
    message_line = f"Пожелание: {cert.message}\n" if cert.message else ""

    body = (
        f"Здравствуйте, {cert.buyer_name}!\n\n"
        f"Ваш подарочный сертификат оплачен.\n\n"
        f"Код сертификата: {cert.code}\n"
        f"Номинал: {value_str}\n"
        f"{recipient_line}"
        f"{message_line}"
        f"Действителен до: {cert.valid_until}\n\n"
        f"Для использования сертификата назовите код при записи или визите.\n"
        f"Проверить баланс: formulatela58.ru/certificates/?code={cert.code}\n\n"
        f"Студия «Формула тела» — 8 (8412) 39-34-33\n"
        f"Пенза, ул. Пушкина, 45"
    )
    try:
        email = EmailMessage(
            subject=f"Ваш сертификат {cert.code} — Формула тела",
            body=body,
            from_email=None,
            to=[order.client_email],
        )
        if pdf_bytes:
            email.attach(
                f"certificate_{cert.code}.pdf",
                pdf_bytes,
                "application/pdf",
            )
        email.send(fail_silently=True)
        return True
    except Exception as exc:
        logger.error("send_certificate_email failed: %s", exc)
        return False


def send_notification_email(subject: str, message: str) -> bool:
    """Отправляет email администраторам.

    Получатели берутся из SiteSettings.notification_emails (через
    get_notification_recipients). Если список пуст — ничего не делает.
    Не пробрасывает исключения.
    """
    recipients = get_notification_recipients()
    if not recipients:
        return False

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
        return True
    except Exception as e:
        logger.error(f"Email notification failed: {e}")
        return False
