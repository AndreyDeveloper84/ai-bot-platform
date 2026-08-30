/**
 * Admin MM2 — invite-master modal (full-screen sheet on Mini App).
 *
 * Route: /admin/team/invite
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM2
 * (lines 327-489). Verbatim Russian copy throughout.
 *
 * Spec quote (§MM2, lines 373-380 — «Что произойдёт» card):
 *
 *   «1. Анна получит сообщение в MAX от помощника салона
 *    2. Откроет ссылку → подтвердит профиль
 *    3. Сможет видеть свой график и клиентов
 *    Ссылка действительна 7 дней.»
 *
 * Spec quote (§MM2, line 397):
 *   «Имя и фамилия | Required; 2–80 chars; allows кириллица + Latin +
 *    spaces + `-` `'`»
 *
 * Two modes:
 *   1. ``invite``       — full form (name + contact + services).
 *      Default. Backend issues invite_token + dispatches MAX DM.
 *   2. ``catalog_only`` — name only. Master exists in catalog for
 *      booking, never gets login. Owner can promote via MM3 later.
 *
 * Submit flow:
 *   ┌─ form ──→ (submit/start) ──→ [POST invite] ──→ confirmation panel
 *   │                                    │
 *   │                                    ├─→ X-Idempotent: «уже есть активное» banner
 *   │                                    └─→ error: inline field OR top banner
 *
 * Confirmation state (§MM2 lines 432-448) offers three CTAs:
 *   - [Открыть профиль]   — disabled tooltip (MM3 detail screen not built)
 *   - [Добавить ещё]      — reset form, stay on screen (onboarding loop)
 *   - [Готово]            — close, return to /admin/team
 *
 * Bridge API:
 *   - BackButton.show() — back returns to /admin/team (or, after success,
 *     also exits — but BackButton stops being shown after submit completes
 *     because at that point the «Готово» / «Добавить ещё» buttons are
 *     primary). We keep BackButton on the form screen only.
 *   - enableClosingConfirmation() while any field is dirty
 *   - HapticFeedback.notificationOccurred('success') on success
 *   - HapticFeedback.notificationOccurred('error') on validation fail
 *
 * Polish item (d) from PR #498 review — the BackButton click handler
 * reads ``stepRef.current`` so the registered effect's empty-deps closure
 * always sees the latest screen-stage. This prevents stale-state bugs
 * when MAX delivers the BackButton tap a few hundred ms after a state
 * change (observed on slow Android devices).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Snackbar } from "../../components/Snackbar";
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
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../../lib/max-sdk";

interface Props {
  me: MeResponse;
}

type Stage = "form" | "success";

/**
 * §MM2 line 397 — кириллица + Latin + spaces + `-` `'`. Allow a wide
 * Latin range (А-я covers Cyrillic incl. Ё; A-Za-z covers Latin).
 */
const NAME_RE = /^[A-Za-zА-Яа-яЁё\s\-']{2,80}$/u;

/** §MM2 line 398 — MAX username OR Russian mobile phone. */
const MAX_USERNAME_RE = /^@[a-z0-9_]{3,40}$/i;
const PHONE_RE = /^\+7\d{10}$/;

const MAX_VISIBLE_SERVICES = 6;

function detectContactMethod(raw: string): InviteContactMethod {
  // Phone wins when the trimmed value starts with `+` — anything else
  // is treated as a MAX username (the regex test still gates submit).
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
    // «25 мая 2026 в 17:00» — drop time to match spec example.
    return d.toLocaleDateString("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

interface FieldErrors {
  name?: string;
  contact_value?: string;
}

export function AdminInviteMasterScreen({ me }: Props) {
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

  // --- toast ---
  const [toast, setToast] = useState<string>("");

  // Dirty tracking — controls enableClosingConfirmation().
  const dirty = useMemo(
    () =>
      stage === "form" &&
      (name.trim().length > 0 ||
        contactValue.trim().length > 0 ||
        selectedServices.size > 0),
    [stage, name, contactValue, selectedServices],
  );

  // --- bridge: BackButton + closing-confirmation. Polish item (d) —
  // use a ref to expose the latest stage to the empty-deps handler.
  const stageRef = useRef<Stage>(stage);
  useEffect(() => {
    stageRef.current = stage;
  }, [stage]);

  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      // Form stage: back exits to /admin/team. Success stage: back also
      // exits (consistent with «Готово»).
      navigate("/admin/team");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setClosingConfirmation(dirty || submitting);
  }, [dirty, submitting]);

  // --- catalog services fetch ---
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
        // Soft-fail — hide the services section per state-matrix branch
        // «Catalog services failed to load».
        setServices([]);
        setServicesFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- handlers ---
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
              // satisfy the backend validator; use an unused max_username
              // placeholder. The backend stores the value but never
              // dispatches a DM because issue_token is False for mode=
              // catalog_only.
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
          // Backend field-level error — surface inline on contact for
          // the typical «service not in tenant» / «contact_value too
          // long» messages, otherwise top banner.
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

  const handleDone = useCallback(() => {
    navigate("/admin/team");
  }, [navigate]);

  // --- derived ---
  const inviteFirstName = useMemo(() => {
    const parts = name.trim().split(/\s+/);
    return parts[0] || "мастер";
  }, [name]);

  const visibleServices = useMemo(() => {
    if (!services) return [];
    return servicesExpanded
      ? services
      : services.slice(0, MAX_VISIBLE_SERVICES);
  }, [services, servicesExpanded]);

  const hiddenServicesCount = useMemo(() => {
    if (!services) return 0;
    if (servicesExpanded) return 0;
    return Math.max(0, services.length - MAX_VISIBLE_SERVICES);
  }, [services, servicesExpanded]);

  // --- role guard. Receptionist isn't supposed to land here at all —
  // the entry button on MM1 hides for non-Owner/non-Admin. If they
  // deep-link in anyway, show a soft notice + back link. The backend
  // re-checks at /invite/ and would 403, but we save the round trip.
  if (!me.is_owner && !me.is_admin) {
    return (
      <div className="screen admin-flow-screen">
        <h1 className="screen__title">Добавление мастера</h1>
        <div className="callout callout--danger" role="alert">
          Только владелец или администратор могут приглашать мастеров.
        </div>
        <button
          type="button"
          className="btn-secondary"
          style={{ marginTop: "var(--s-3)" }}
          onClick={() => navigate("/admin/team")}
        >
          Вернуться к команде
        </button>
      </div>
    );
  }

  // --- success view ---
  if (stage === "success" && result) {
    const expiresHuman = formatExpiresHuman(result.invite_expires_at);
    const isCatalogOnly = result.invite_token === null;

    return (
      <div className="screen admin-flow-screen">
        <div style={{ textAlign: "center", padding: "var(--s-4) 0" }}>
          <div
            style={{ fontSize: "48px", lineHeight: 1 }}
            aria-hidden="true"
          >
            ✓
          </div>
          <h1 className="screen__title" style={{ marginTop: "var(--s-3)" }}>
            {isCatalogOnly
              ? `${inviteFirstName} добавлен(а) в каталог`
              : "Приглашение отправлено"}
          </h1>

          {result.was_idempotent && (
            <div
              className="callout"
              role="status"
              style={{
                marginTop: "var(--s-3)",
                maxWidth: "360px",
                marginInline: "auto",
                textAlign: "start",
              }}
            >
              {`У этого мастера уже есть активное приглашение${expiresHuman ? `. Срок действия до ${expiresHuman}` : ""}.`}
            </div>
          )}

          {!isCatalogOnly && (
            <>
              <p style={{ marginTop: "var(--s-3)" }}>
                {result.max_dm_delivery === "failed"
                  ? `Отправить сообщение ${inviteFirstName} в MAX не удалось — передайте ссылку сами.`
                  : `${inviteFirstName} получит сообщение от помощника салона в MAX в течение минуты.`}
              </p>
              {expiresHuman && (
                <p style={{ color: "var(--c-text-secondary)" }}>
                  {`Ссылка действительна до ${expiresHuman} (через 7 дней).`}
                </p>
              )}
              {/*
                Ссылка показывается ВСЕГДА, а не только при `max_dm_delivery
                === "failed"` (решение владельца 30.08).

                Прежнее условие исходило из того, что успешная отправка = мастер
                приглашение получил. Это разные вещи: бот сообщает, что ПРИНЯЛ
                отправку, а подтверждения доставки у нас нет вовсе. При молчаливой
                потере владелец видел «получит в течение минуты», ссылки не имел,
                и достать её мог только запросом к базе — так и пришлось сделать
                для первого живого приглашения на пилоте (Екатерина, 30.08).

                Показ ссылки при удачной отправке ничего не стоит и ничего не
                раскрывает: она и так уходит тому же человеку. Скрытие же
                превращало отсутствие обратной связи в видимость успеха.
              */}
              {result.fallback_link && (
                <div
                  className="callout"
                  role="status"
                  style={{
                    marginTop: "var(--s-3)",
                    maxWidth: "360px",
                    marginInline: "auto",
                    textAlign: "start",
                  }}
                >
                  {"Ссылка приглашения — можно отправить самому: "}
                  <code style={{ wordBreak: "break-all" }}>
                    {result.fallback_link}
                  </code>
                </div>
              )}
            </>
          )}

          {isCatalogOnly && (
            <p style={{ marginTop: "var(--s-3)" }}>
              {`${inviteFirstName} появится в каталоге, но не получит доступ. Можно пригласить позже.`}
            </p>
          )}

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--s-2)",
              marginTop: "var(--s-5)",
              maxWidth: "320px",
              marginInline: "auto",
            }}
          >
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Доступно после MM3 detail screen"
            >
              {`Открыть профиль ${inviteFirstName}`}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={resetForm}
            >
              Добавить ещё мастера
            </button>
            <button
              type="button"
              className="cta-bar__button"
              onClick={handleDone}
            >
              Готово
            </button>
          </div>
        </div>
      </div>
    );
  }

  // --- form view ---
  const disabledRoleTooltip = "Скоро — для Owner";
  const disabledEmailTooltip = "Скоро — пока используйте MAX";

  return (
    <div className="screen admin-flow-screen">
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--s-3)",
        }}
      >
        <h1 className="screen__title" style={{ margin: 0 }}>
          Новый мастер
        </h1>
        <button
          type="button"
          className="btn-secondary"
          aria-label="Закрыть"
          onClick={() => navigate("/admin/team")}
          style={{ minWidth: "44px", padding: "var(--s-2)" }}
        >
          ✕
        </button>
      </header>

      {bannerOffline && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
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
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginBottom: "var(--s-3)" }}
        >
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

      {/* Mode toggle */}
      <fieldset
        style={{
          border: 0,
          padding: 0,
          margin: "0 0 var(--s-4)",
        }}
      >
        <legend
          style={{ marginBottom: "var(--s-2)", fontWeight: 500 }}
        >
          Как добавить?
        </legend>
        <label
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--s-2)",
            padding: "var(--s-2) 0",
          }}
        >
          <input
            type="radio"
            name="invite-mode"
            checked={mode === "invite"}
            onChange={() => setMode("invite")}
          />
          <span>Пригласить через MAX</span>
        </label>
        <label
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--s-2)",
            padding: "var(--s-2) 0",
          }}
        >
          <input
            type="radio"
            name="invite-mode"
            checked={mode === "catalog_only"}
            onChange={() => setMode("catalog_only")}
          />
          <span>Без приглашения (только запись в каталоге)</span>
        </label>
      </fieldset>

      {/* Name field — required in both modes */}
      <div style={{ marginBottom: "var(--s-3)" }}>
        <label
          htmlFor="invite-name"
          style={{ display: "block", marginBottom: "var(--s-1)" }}
        >
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
          <div
            id="invite-name-err"
            className="callout callout--danger"
            role="alert"
            style={{ marginTop: "var(--s-1)" }}
          >
            {fieldErrors.name}
          </div>
        )}
      </div>

      {mode === "invite" && (
        <>
          {/* Contact for invite */}
          <fieldset
            style={{ border: 0, padding: 0, margin: "0 0 var(--s-3)" }}
          >
            <legend style={{ marginBottom: "var(--s-1)" }}>
              Контакт для приглашения *
            </legend>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                padding: "var(--s-2) 0",
              }}
            >
              <input type="radio" name="contact-kind" checked readOnly />
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
              style={{ marginInlineStart: "var(--s-5)" }}
            />
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                padding: "var(--s-2) 0",
                opacity: 0.55,
              }}
              title={disabledEmailTooltip}
            >
              <input type="radio" name="contact-kind" disabled />
              <span>Email (если MAX не работает)</span>
            </label>
            {fieldErrors.contact_value && (
              <div
                id="invite-contact-err"
                className="callout callout--danger"
                role="alert"
                style={{ marginTop: "var(--s-1)" }}
              >
                {fieldErrors.contact_value}
              </div>
            )}
          </fieldset>

          {/* Role — only Master enabled this PR */}
          <fieldset
            style={{ border: 0, padding: 0, margin: "0 0 var(--s-3)" }}
          >
            <legend style={{ marginBottom: "var(--s-1)" }}>Роль *</legend>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                padding: "var(--s-2) 0",
              }}
            >
              <input type="radio" name="invite-role" checked readOnly />
              <span>Мастер (видит только своих клиентов)</span>
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                padding: "var(--s-2) 0",
                opacity: 0.55,
              }}
              title={disabledRoleTooltip}
            >
              <input type="radio" name="invite-role" disabled />
              <span>Администратор (видит всех)</span>
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "var(--s-2)",
                padding: "var(--s-2) 0",
                opacity: 0.55,
              }}
              title={disabledRoleTooltip}
            >
              <input type="radio" name="invite-role" disabled />
              <span>Ресепшен (видит, не редактирует)</span>
            </label>
          </fieldset>

          {/* Services — optional */}
          {servicesFailed ? (
            <div
              className="callout"
              role="status"
              style={{ marginBottom: "var(--s-3)" }}
            >
              Услуги — добавьте после создания
            </div>
          ) : services && services.length > 0 ? (
            <fieldset
              style={{ border: 0, padding: 0, margin: "0 0 var(--s-3)" }}
            >
              <legend style={{ marginBottom: "var(--s-1)" }}>
                Услуги (можно указать сразу или потом)
              </legend>
              {visibleServices.map((s) => (
                <label
                  key={s.id}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: "var(--s-2)",
                    padding: "var(--s-2) 0",
                  }}
                >
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
                  style={{ marginTop: "var(--s-2)" }}
                  onClick={() => setServicesExpanded(true)}
                >
                  {`Показать все услуги (${services.length}) →`}
                </button>
              )}
            </fieldset>
          ) : null}

          {/* Schedule — read-only with disabled edit */}
          <div style={{ marginBottom: "var(--s-3)" }}>
            <div style={{ marginBottom: "var(--s-1)" }}>
              График (применится автоматически — можно изменить)
            </div>
            <div
              className="callout"
              role="status"
              style={{ marginBottom: "var(--s-1)" }}
            >
              Пн-Пт 10:00–19:00, Сб-Вс выходной
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled
              title="Доступно после создания"
            >
              Изменить график →
            </button>
          </div>

          {/* «Что произойдёт» explainer */}
          <div
            className="callout"
            style={{ marginBottom: "var(--s-4)" }}
          >
            <div style={{ fontWeight: 600, marginBottom: "var(--s-2)" }}>
              Что произойдёт
            </div>
            <ol style={{ margin: 0, paddingInlineStart: "var(--s-4)" }}>
              <li>
                {`${inviteFirstName} получит сообщение в MAX от помощника салона`}
              </li>
              <li>Откроет ссылку → подтвердит профиль</li>
              <li>Сможет видеть свой график и клиентов</li>
            </ol>
            <p style={{ margin: "var(--s-2) 0 0" }}>
              Ссылка действительна 7 дней.
            </p>
          </div>
        </>
      )}

      {mode === "catalog_only" && (
        <div
          className="callout"
          style={{ marginBottom: "var(--s-4)" }}
        >
          Мастер появится в каталоге, но не получит доступ. Можно пригласить
          позже.
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: "var(--s-2)",
          marginTop: "var(--s-4)",
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          style={{ flex: 1 }}
          disabled={submitting}
          onClick={() => navigate("/admin/team")}
        >
          Отмена
        </button>
        <button
          type="button"
          className="cta-bar__button"
          style={{ flex: 1 }}
          disabled={submitting}
          onClick={() => void handleSubmit()}
        >
          {submitting
            ? "Отправляю приглашение…"
            : mode === "invite"
              ? "Пригласить →"
              : "Добавить в каталог →"}
        </button>
      </div>

      <Snackbar
        visible={!!toast}
        message={toast}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />
    </div>
  );
}
