/**
 * «Добавить человека» — ветка «нового мастера ещё нет в салоне».
 *
 * Половина одного экрана (`AdminAddPersonScreen`), а не самостоятельный
 * экран: наверху её тела рисуется переключатель веток, который
 * приходит пропом. Всё остальное — прежний `AdminInviteMasterScreen`
 * (MM2), перенесённый на общий каркас `ScreenLayout` и дополненный тем,
 * ради чего затевалась DRF-1505.
 *
 * Спека: docs/design/handoffs/2026-05-18-master-management-handoff.md
 * §MM2 (строки 327-489), русские тексты дословно.
 *
 * # Что изменилось против прежнего экрана
 *
 * **Ссылка-приглашение.** Бэкенд отдаёт `invite_link` с DRF-1424 —
 * `https://max.ru/<салонный бот>?start=master_invite_<токен>`. В типе
 * ответа мини-аппа этого поля не было вовсе, поэтому единственный
 * работающий способ передачи до владельца не доходил: у него оставалось
 * личное сообщение, которое уходит КЛИЕНТСКИМ ботом и достигает только
 * тот чат, который уже существует. Незнакомому мастеру — никогда.
 *
 * **Честный отказ доставки.** `max_dm_delivery` мог сказать `failed`, а
 * экран говорил «перешлите ссылку ниже», когда никакой ссылки ниже не
 * было. Причина (`max_dm_error`) не показывалась вовсе. Теперь причина
 * названа, и названа по-разному: «MAX не нашёл такой аккаунт» —
 * владелец правит опечатку прямо здесь; «вход в мини-приложение не
 * настроен» — правит не владелец, и врать ему, что «получит сообщение в
 * течение минуты», больше нельзя.
 *
 * # Чего эта ветка НЕ делает
 *
 * Не чинит личное сообщение. Оно уходит клиентским ботом
 * (`send_message` без `bot=`, `apps/admin_api/views_invite.py`), и это
 * тупик по конструкции, а не дефект настройки: правильный путь —
 * ссылка. Заведено отдельно.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ScreenLayout } from "../../components/ScreenLayout";
import { ShareableLink } from "../../components/ShareableLink";
import { StickyCta } from "../../components/StickyCta";
import { useClosingConfirmation } from "../../hooks/useClosingConfirmation";
import { ApiError } from "../../lib/api";
import {
  getCatalogServicesForAdmin,
  inviteMaster,
  type CatalogServiceLite,
  type InviteContactMethod,
  type InviteMasterResponse,
  type InviteMode,
  type MeResponse,
} from "../../lib/admin-api";
import { hapticNotify, hapticSelection } from "../../lib/max-sdk";
import { backTo, screenRoot } from "../../lib/screen-back";
import type { ReactNode } from "react";

interface Props {
  readonly me: MeResponse;
  /** Переключатель веток — рисуется первым в теле, до формы. */
  readonly switcher: ReactNode;
}

type Stage = "form" | "success";

/**
 * §MM2 line 397 — кириллица + Latin + spaces + `-` `'`.
 */
const NAME_RE = /^[A-Za-zА-Яа-яЁё\s\-']{2,80}$/u;

/** §MM2 line 398 — MAX username OR Russian mobile phone. */
const MAX_USERNAME_RE = /^@[a-z0-9_]{3,40}$/i;
const PHONE_RE = /^\+7\d{10}$/;

const MAX_VISIBLE_SERVICES = 6;

function detectContactMethod(raw: string): InviteContactMethod {
  const trimmed = raw.trim();
  return trimmed.startsWith("+") ? "max_phone" : "max_username";
}

function validateContactValue(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("+")) return PHONE_RE.test(trimmed);
  return MAX_USERNAME_RE.test(trimmed);
}

function formatExpiresHuman(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export interface DeliveryNotice {
  /** `ok` — сообщение принято; `warn` — не ушло, но есть чем заменить. */
  readonly tone: "ok" | "warn" | "danger";
  readonly text: string;
}

/**
 * Что сказать владельцу про личное сообщение — по слову бэкенда.
 *
 * Экспортируется ради теста: правил здесь пять, а глазами на экране
 * проверяется одно за прогон.
 *
 * Три вещи, которые эта функция обязана держать врозь:
 *
 * 1. **Принято ≠ доставлено.** `queued` означает, что MAX принял
 *    отправку. Подтверждения доставки у нас нет вовсе, и прежний текст
 *    «получит сообщение в течение минуты» обещал за MAX то, чего MAX не
 *    обещал. Пилот 30.08: сообщение «ушло», мастер его не увидел,
 *    ссылку пришлось доставать запросом к базе.
 * 2. **Опечатку правит владелец, настройку — нет.** `max_status_404`
 *    значит «такого аккаунта нет» — поле с MAX-аккаунтом на этом же
 *    экране. `no_entry_configured` владелец не исправит ничем, и
 *    посылать его перепроверять правильный аккаунт — злее, чем молчать.
 * 3. **Отложенное ≠ сломанное.** По телефону сообщение не уходит
 *    потому, что мы не построили поиск чата по номеру, а не потому что
 *    что-то отказало. Слово «не удалось» здесь было бы ложью в другую
 *    сторону.
 */
export function deliveryNotice(
  result: InviteMasterResponse,
  firstName: string,
): DeliveryNotice {
  const hasLink = Boolean(result.invite_link);

  if (result.max_dm_delivery === "queued") {
    return {
      tone: "ok",
      text:
        `Сообщение для ${firstName} передано в MAX. Подтверждения доставки ` +
        "у нас нет — если он(а) ничего не увидит, отправьте ссылку ниже сами.",
    };
  }

  if (result.max_dm_delivery === "skipped") {
    if (result.max_dm_error === "max_phone_lookup_deferred") {
      return {
        tone: "warn",
        text:
          "По номеру телефона бот написать пока не умеет — такого поиска мы " +
          "ещё не построили. Отправьте ссылку любым способом: она работает " +
          "и без переписки с ботом.",
      };
    }
    return {
      tone: "warn",
      text: "Сообщение не отправлялось. Передайте ссылку сами.",
    };
  }

  // failed
  if (result.max_dm_error === "no_entry_configured") {
    return {
      tone: hasLink ? "warn" : "danger",
      text:
        "Сообщение НЕ отправлено: в этом контуре не настроен вход в " +
        "мини-приложение, и боту нечего было положить в письмо. Это " +
        `настройка на стороне платформы — ${firstName} тут ни при чём, и ` +
        "повторная отправка ничего не изменит.",
    };
  }
  if (result.max_dm_error.startsWith("max_status_")) {
    const status = result.max_dm_error.slice("max_status_".length);
    return {
      tone: "warn",
      text:
        `MAX отказался принять сообщение (код ${status}). Обычно это значит, ` +
        "что такого MAX-аккаунта нет — проверьте написание. Ссылка ниже " +
        "работает независимо от аккаунта.",
    };
  }
  return {
    tone: "warn",
    text:
      `Сообщение для ${firstName} отправить не удалось. Передайте ссылку ` +
      "сами — она работает независимо от переписки.",
  };
}

interface FieldErrors {
  name?: string;
  contact_value?: string;
}

export function AddPersonNewMasterSection({ me, switcher }: Props) {
  const navigate = useNavigate();

  // --- form state ---
  const [mode, setMode] = useState<InviteMode>("invite");
  const [name, setName] = useState<string>("");
  const [contactValue, setContactValue] = useState<string>("");
  const [selectedServices, setSelectedServices] = useState<Set<string>>(
    () => new Set(),
  );
  const [servicesExpanded, setServicesExpanded] = useState<boolean>(false);

  // --- catalog services ---
  const [services, setServices] = useState<CatalogServiceLite[] | null>(null);
  const [servicesFailed, setServicesFailed] = useState<boolean>(false);

  // --- submit state ---
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [bannerError, setBannerError] = useState<string>("");
  const [bannerOffline, setBannerOffline] = useState<boolean>(false);

  // --- success state ---
  const [stage, setStage] = useState<Stage>("form");
  const [result, setResult] = useState<InviteMasterResponse | null>(null);

  const dirty =
    stage === "form" &&
    (name.trim().length > 0 ||
      contactValue.trim().length > 0 ||
      selectedServices.size > 0);

  // На успехе подтверждение выхода держится не ради формы: на экране
  // лежит ссылка-приглашение, и уход по свайпу уносит её вместе с
  // экраном. Достать её потом можно только запросом к базе — так и
  // пришлось делать для первого живого приглашения на пилоте.
  useClosingConfirmation(dirty || submitting || stage === "success");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await getCatalogServicesForAdmin();
        if (cancelled) return;
        setServices(list);
        setServicesFailed(false);
      } catch {
        if (cancelled) return;
        setServices([]);
        setServicesFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleService = useCallback((id: string) => {
    setSelectedServices((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const resetForm = useCallback(() => {
    setMode("invite");
    setName("");
    setContactValue("");
    setSelectedServices(new Set());
    setServicesExpanded(false);
    setFieldErrors({});
    setBannerError("");
    setBannerOffline(false);
    setResult(null);
    setStage("form");
  }, []);

  const validate = useCallback((): FieldErrors => {
    const errs: FieldErrors = {};
    const trimmedName = name.trim();
    if (!trimmedName) {
      errs.name = "Укажите имя и фамилию";
    } else if (!NAME_RE.test(trimmedName)) {
      errs.name = "Имя 2–80 символов: буквы, пробелы, «-» или «'»";
    }
    if (mode === "invite") {
      const trimmedContact = contactValue.trim();
      if (!trimmedContact) {
        errs.contact_value = "Укажите MAX-аккаунт или телефон";
      } else if (!validateContactValue(trimmedContact)) {
        errs.contact_value =
          "Формат: @username (от 3 до 40 латинских символов) или +7XXXXXXXXXX";
      }
    }
    return errs;
  }, [name, contactValue, mode]);

  const handleSubmit = useCallback(async () => {
    if (submitting) return;
    setBannerError("");
    setBannerOffline(false);

    const errs = validate();
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      hapticNotify("error");
      return;
    }
    setFieldErrors({});

    setSubmitting(true);
    try {
      const trimmedName = name.trim();
      const trimmedContact = contactValue.trim();
      const serviceIds = Array.from(selectedServices);
      const payload =
        mode === "invite"
          ? {
              name: trimmedName,
              contact_method: detectContactMethod(trimmedContact),
              contact_value: trimmedContact,
              services: serviceIds,
              schedule_preset: "default_mon_fri_10_19" as const,
              mode: "invite" as const,
            }
          : {
              name: trimmedName,
              // Catalog-only mode still needs a contact_method/value to
              // satisfy the backend validator; use an unused
              // max_username placeholder. The backend stores the value
              // but never dispatches a DM because issue_token is False
              // for mode=catalog_only.
              contact_method: "max_username" as const,
              contact_value: `@catalog_only_${Date.now()}`,
              services: serviceIds,
              schedule_preset: "none" as const,
              mode: "catalog_only" as const,
            };
      const res = await inviteMaster(payload);
      hapticNotify("success");
      setResult(res);
      setStage("success");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 400) {
          if (e.detail.toLowerCase().includes("contact")) {
            setFieldErrors({ contact_value: e.detail });
          } else if (e.detail.toLowerCase().includes("name")) {
            setFieldErrors({ name: e.detail });
          } else {
            setBannerError(e.detail || "Не получилось отправить");
          }
        } else if (e.status >= 500) {
          setBannerError("MAX не отвечает — попробуйте позже");
        } else {
          setBannerError(e.detail || "Не получилось отправить");
        }
      } else {
        setBannerOffline(true);
      }
      hapticNotify("error");
    } finally {
      setSubmitting(false);
    }
  }, [submitting, validate, name, contactValue, mode, selectedServices]);

  const handleDone = useCallback(() => navigate("/admin/team"), [navigate]);

  const inviteFirstName = useMemo(() => {
    const parts = name.trim().split(/\s+/);
    return parts[0] || "мастер";
  }, [name]);

  const visibleServices = useMemo(() => {
    if (!services) return [];
    return servicesExpanded ? services : services.slice(0, MAX_VISIBLE_SERVICES);
  }, [services, servicesExpanded]);

  const hiddenServicesCount = useMemo(() => {
    if (!services) return 0;
    if (servicesExpanded) return 0;
    return Math.max(0, services.length - MAX_VISIBLE_SERVICES);
  }, [services, servicesExpanded]);

  // --- role guard. Ресепшен сюда не ведут кнопки, но deep link может.
  if (!me.is_owner && !me.is_admin) {
    return (
      <ScreenLayout back={backTo("/admin/team")} title="Добавить человека">
        <div className="callout callout--danger" role="alert">
          Только владелец или администратор могут добавлять людей в салон.
        </div>
      </ScreenLayout>
    );
  }

  // --- success view ---
  if (stage === "success" && result) {
    const expiresHuman = formatExpiresHuman(result.invite_expires_at);
    const isCatalogOnly = result.invite_token === null;
    const notice = isCatalogOnly ? null : deliveryNotice(result, inviteFirstName);

    return (
      <ScreenLayout
        // Возврата отсюда нет по той же причине, что у кода доступа:
        // ссылка живёт только на этом экране, а уход назад ведёт на
        // список команды, где её уже не достать.
        back={screenRoot(
          "Ссылка-приглашение показывается здесь и больше нигде: уход " +
            "назад уносит её вместе с экраном. Выход — «Готово».",
        )}
        title={
          isCatalogOnly
            ? `${inviteFirstName} добавлен(а) в каталог`
            : "Приглашение готово"
        }
        cta={<StickyCta onClick={handleDone}>Готово</StickyCta>}
      >
        {result.was_idempotent && (
          <div className="callout" role="status">
            {`У этого мастера уже есть активное приглашение${
              expiresHuman ? `. Срок действия до ${expiresHuman}` : ""
            }. Ссылка ниже — от него, новое не создавалось.`}
          </div>
        )}

        {isCatalogOnly ? (
          <p>
            {`${inviteFirstName} появится в каталоге, но не получит доступ в ` +
              "кабинет. Пригласить можно позже."}
          </p>
        ) : (
          <>
            {notice && (
              <div
                className={
                  notice.tone === "ok"
                    ? "callout"
                    : notice.tone === "warn"
                      ? "callout callout--warn"
                      : "callout callout--danger"
                }
                role={notice.tone === "ok" ? "status" : "alert"}
              >
                {notice.text}
              </div>
            )}

            {result.invite_link ? (
              <ShareableLink
                url={result.invite_link}
                label="Ссылка-приглашение"
                hint={
                  `Отправьте её ${inviteFirstName} любым способом — в MAX, ` +
                  "в другом мессенджере, по SMS. Кто её откроет, попадёт в " +
                  "диалог с ботом салона и получит кнопку входа. Приглашение " +
                  "при открытии не тратится: принять его сможет только тот, " +
                  "кому оно выдано."
                }
              />
            ) : (
              <div className="callout callout--danger" role="alert">
                Ссылки нет: в этом контуре не настроен салонный бот, а без него
                передать приглашение нечем. Это настройка платформы — сообщите
                нам, сами вы это не почините.
              </div>
            )}

            {expiresHuman && (
              <p className="admin-hint">
                {`Приглашение действительно до ${expiresHuman} — семь дней.`}
              </p>
            )}

            {result.fallback_link && (
              <details className="admin-details">
                <summary>Веб-адрес анкеты — только внутри MAX</summary>
                <p>
                  Открывать его нужно ВНУТРИ MAX. В браузере вход не сработает:
                  приложению неоткуда узнать, кто открыл. Ссылка выше делает то
                  же самое и не требует этой оговорки, так что этот адрес нужен
                  редко.
                </p>
                <code className="shareable__url">{result.fallback_link}</code>
              </details>
            )}
          </>
        )}

        <button type="button" className="admin-flow-back" onClick={resetForm}>
          Добавить ещё человека
        </button>
      </ScreenLayout>
    );
  }

  // --- form view ---
  const disabledEmailTooltip = "Скоро — пока используйте MAX";

  return (
    <ScreenLayout
      back={backTo("/admin/team")}
      title="Добавить человека"
      cta={
        <StickyCta onClick={() => void handleSubmit()} disabled={submitting}>
          {submitting
            ? "Отправляю приглашение…"
            : mode === "invite"
              ? "Пригласить"
              : "Добавить в каталог"}
        </StickyCta>
      }
    >
      {switcher}

      {bannerOffline && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>Связь пропала. Проверьте интернет.</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => void handleSubmit()}
            disabled={submitting}
          >
            Повторить
          </button>
        </div>
      )}

      {bannerError && !bannerOffline && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{bannerError}</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => void handleSubmit()}
            disabled={submitting}
          >
            Повторить
          </button>
        </div>
      )}

      <fieldset className="admin-fieldset">
        <legend className="admin-fieldset__legend">Как добавить?</legend>
        <label className="admin-choice">
          <input
            type="radio"
            name="invite-mode"
            aria-label="Пригласить через MAX"
            checked={mode === "invite"}
            onChange={() => {
              hapticSelection();
              setMode("invite");
            }}
          />
          <span>Пригласить через MAX</span>
        </label>
        <label className="admin-choice">
          <input
            type="radio"
            name="invite-mode"
            aria-label="Без приглашения"
            checked={mode === "catalog_only"}
            onChange={() => {
              hapticSelection();
              setMode("catalog_only");
            }}
          />
          <span>Без приглашения (только запись в каталоге)</span>
        </label>
      </fieldset>

      <div className="admin-field">
        <label htmlFor="invite-name" className="admin-field__title">
          Имя и фамилия *
        </label>
        <input
          id="invite-name"
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (fieldErrors.name) {
              setFieldErrors((prev) => ({ ...prev, name: undefined }));
            }
          }}
          placeholder="Анна Петрова"
          maxLength={80}
          className="admin-search-input"
          aria-invalid={Boolean(fieldErrors.name)}
          aria-describedby={fieldErrors.name ? "invite-name-err" : undefined}
        />
        {fieldErrors.name && (
          <div id="invite-name-err" className="callout callout--danger" role="alert">
            {fieldErrors.name}
          </div>
        )}
      </div>

      {mode === "invite" && (
        <>
          <fieldset className="admin-fieldset">
            <legend className="admin-fieldset__legend">
              Контакт для приглашения *
            </legend>
            <label className="admin-choice">
              <input
                type="radio"
                name="contact-kind"
                aria-label="MAX-аккаунт"
                checked
                readOnly
              />
              <span>MAX-аккаунт (username или phone)</span>
            </label>
            <input
              id="invite-contact"
              type="text"
              value={contactValue}
              onChange={(e) => {
                setContactValue(e.target.value);
                if (fieldErrors.contact_value) {
                  setFieldErrors((prev) => ({
                    ...prev,
                    contact_value: undefined,
                  }));
                }
              }}
              placeholder="@anna_styl или +79051234567"
              inputMode="text"
              autoCapitalize="off"
              autoCorrect="off"
              maxLength={128}
              className="admin-search-input"
              aria-invalid={Boolean(fieldErrors.contact_value)}
              aria-describedby={
                fieldErrors.contact_value ? "invite-contact-err" : undefined
              }
            />
            <p className="admin-hint">
              Аккаунт нужен только для сообщения. Если его нет или он написан с
              ошибкой — приглашение всё равно создастся, и вы передадите ссылку
              сами.
            </p>
            <label
              className="admin-choice admin-choice--disabled"
              title={disabledEmailTooltip}
            >
              <input type="radio" name="contact-kind" aria-label="Email" disabled />
              <span>Email (если MAX не работает)</span>
            </label>
            {fieldErrors.contact_value && (
              <div
                id="invite-contact-err"
                className="callout callout--danger"
                role="alert"
              >
                {fieldErrors.contact_value}
              </div>
            )}
          </fieldset>

          <fieldset className="admin-fieldset">
            <legend className="admin-fieldset__legend">Роль *</legend>
            <label className="admin-choice">
              <input
                type="radio"
                name="invite-role"
                aria-label="Мастер"
                checked
                readOnly
              />
              <span>Мастер (видит только своих клиентов)</span>
            </label>
            <p className="admin-hint">
              Администратора и ресепшен заводят кодом доступа — переключатель
              наверху.
            </p>
          </fieldset>

          {servicesFailed ? (
            <div className="callout" role="status">
              Услуги — добавьте после создания
            </div>
          ) : services && services.length > 0 ? (
            <fieldset className="admin-fieldset">
              <legend className="admin-fieldset__legend">
                Услуги (можно указать сразу или потом)
              </legend>
              {visibleServices.map((s) => (
                <label key={s.id} className="admin-choice">
                  <input
                    type="checkbox"
                    checked={selectedServices.has(s.id)}
                    onChange={() => toggleService(s.id)}
                  />
                  <span>{s.name}</span>
                </label>
              ))}
              {hiddenServicesCount > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setServicesExpanded(true)}
                >
                  {`Показать все услуги (${services.length}) →`}
                </button>
              )}
            </fieldset>
          ) : null}

          <div className="admin-field">
            <div className="admin-field__title">
              График (применится автоматически — можно изменить)
            </div>
            <div className="callout" role="status">
              Пн-Пт 10:00–19:00, Сб-Вс выходной
            </div>
          </div>

          <div className="callout">
            <div style={{ fontWeight: 600, marginBottom: "var(--s-2)" }}>
              Что произойдёт
            </div>
            <ol style={{ margin: 0, paddingInlineStart: "var(--s-4)" }}>
              <li>Вы получите ссылку-приглашение и передадите её.</li>
              <li>
                {`${inviteFirstName} откроет ссылку → подтвердит профиль`}
              </li>
              <li>Сможет видеть свой график и клиентов</li>
            </ol>
            <p style={{ margin: "var(--s-2) 0 0" }}>
              Ссылка действительна 7 дней. Бот попробует написать сам, но
              получится это только если переписка с ним уже была.
            </p>
          </div>
        </>
      )}

      {mode === "catalog_only" && (
        <div className="callout">
          Мастер появится в каталоге, но не получит доступ. Можно пригласить
          позже.
        </div>
      )}
    </ScreenLayout>
  );
}
