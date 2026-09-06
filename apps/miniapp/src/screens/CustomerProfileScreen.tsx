/**
 * Customer profile screen — Tier 1 Priority 6 Phase B (deferred scope).
 *
 * Route: `/customer/profile`
 *
 * Spec: `docs/screens/customer-profile-flow.md` (deferred Variant 3,
 * post commit `376784e`). Sections R1-R6; R2 export/delete are wired
 * to the C5 152-ФЗ endpoints since pilot phase 2a (PILOT_CONTRACTS
 * §6); only R3 memory stays deferred per §0 «Pilot scope & backend
 * reality» recon.
 *
 * # Section order (per spec §11.1 selected variant)
 *   R1 — header (avatar initials fallback + name; handle/scope rows
 *        render only when the real /me provides them)
 *   R2 — consent: 2 locked rows + marketing toggle (real notify_promo)
 *        + health-consent row + §4.2 accordion
 *        + «Запросить данные» / «Удалить аккаунт» → C5 sheets
 *        (PersonalDataSheets.tsx; support deeplink = error fallback)
 *   R3 — memory transparency: coming-soon card (no data, no clear)
 *   R4 — proactive AI toggle: HIDDEN until DRF-1520 (owner 05.09 —
 *        no non-working toggles)
 *   R5 — notifications: MAX channel + soft timing + entry → support
 *   R6 — states: loading skeleton / API-down with retry / offline
 *        banner / toggle-change snackbar
 *
 * # WCAG 2.2 AA inline (per spec §13 — 12 patterns)
 *   2.5.8 — all interactive ≥44dp
 *   1.4.3 — body text uses `--c-text-primary`; muted via secondary
 *   1.3.1 — `<dl>` for consent rows + role=switch on toggles
 *   2.4.3 — vertical R1→R6 focus order; row order within R2
 *   2.4.1 — skip link to R2 («К управлению приватностью»)
 *   1.4.4 — em-sized body text; resize tolerant via tokens
 *   2.3.3 — shimmer + chevron rotate gated on prefers-reduced-motion
 *   4.1.3 — toggle change announces via aria-live (Snackbar)
 *   3.1.1 — lang="ru" inherited; `Ayla` wrapped lang="en" in copy
 *   4.1.2 — explicit aria-label on every toggle
 *   2.5.5 — support sheets confirm before destructive deeplink
 *   3.3.4 — sheet primary CTA NOT auto-focused
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ComingSoonCard } from "../components/ComingSoonCard";
import { ConsentRow } from "../components/ConsentRow";
import { DisclosureSheet } from "../components/DisclosureSheet";
import { NotificationCard } from "../components/NotificationCard";
import {
  HealthConsentSheet,
  PersonalDataDeleteSheet,
  PersonalDataExportSheet,
} from "../components/PersonalDataSheets";
import { Skeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import {
  additionalSalonsLabel,
  avatarInitials,
  fetchConsents,
  fetchMe,
  formatConsentDate,
  setMarketingConsent,
  type ConsentsResponse,
  type MeProfileResponse,
} from "../lib/customer-profile";
import {
  fetchHealthConsent,
  type HealthConsentState,
} from "../lib/health-consent";
import { SurfaceSwitchButton } from "../components/SurfaceSwitch";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

// ---------------------------------------------------------------------------
// Реальные данные (DRF-1475, часть Б, решение владельца 05.09): R1
// identity header и R2 consent rows + marketing toggle читают/пишут
// настоящий `/customer/me` (`customer-profile.ts` → lib/api.ts), имя и
// маркетинговое согласие видны и работают в проде. Секции, у которых
// пока нет backend («Подсказки от Ayla», «Хранение данных»), из
// рендера УБРАНЫ — не disabled, не «скоро»: тумблер, который человек
// двигает, а он ничего не делает, врёт про наличие контроля и хуже
// отсутствующего. Возврат — после DRF-1520 (см. TODO у мест скрытия).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Loading / error / offline state machine.
// ---------------------------------------------------------------------------

type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | {
      kind: "ready";
      me: MeProfileResponse;
      consents: ConsentsResponse;
    };

interface ToastState {
  visible: boolean;
  message: string;
}

const EMPTY_TOAST: ToastState = { visible: false, message: "" };

export function CustomerProfileScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом клиентской поверхности.
  //
  // Профиль — вкладка со своей нижней навигацией, и стрелка у него
  // была и раньше. DRF-1493 не снимает существующие органы
  // управления, а доводит их до работающего состояния: стрелка
  // остаётся, но ведёт в заданное место, а не в историю, которой у
  // пришедшего по deep link нет. Нужна ли вкладке стрелка вообще —
  // вопрос раскладки поверхности, он у DRF-1481.
  const onBack = useScreenBack(backTo("/customer/main"));
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [toast, setToast] = useState<ToastState>(EMPTY_TOAST);
  const [marketingBusy, setMarketingBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null);
  // Согласие на медданные (DRF-1453) — РЕАЛЬНЫЙ эндпоинт, у него свой
  // ресурс вне общего `Status` и своя загрузка: строка обязана быть
  // видна в проде всегда. Пока состояние неизвестно (`null`) строка
  // показывает «загружаю» и не кликается — «Не разрешено» до ответа
  // сервера было бы утверждением, которого мы не проверяли.
  const [healthConsent, setHealthConsent] = useState<HealthConsentState | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [healthSheetOpen, setHealthSheetOpen] = useState(false);
  const healthTriggerRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const [me, consents] = await Promise.all([fetchMe(), fetchConsents()]);
      setStatus({ kind: "ready", me, consents });
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const loadHealthConsent = useCallback(async () => {
    setHealthFailed(false);
    try {
      setHealthConsent(await fetchHealthConsent());
    } catch {
      // Читать не смогли — строка честно говорит об этом и предлагает
      // повторить. Показать «Не разрешено» было бы удобнее и неверно.
      setHealthConsent(null);
      setHealthFailed(true);
    }
  }, []);

  useEffect(() => {
    loadHealthConsent();
  }, [loadHealthConsent]);

  // Состояние словами. Три исхода, и ни один не притворяется другим:
  // неизвестно / не смогли прочитать / известное да-нет с датой выдачи.
  const healthStatusText = healthFailed
    ? "Не удалось загрузить"
    : healthConsent === null
      ? "Загружаю"
      : healthConsent.granted
        ? `Разрешено ${
            healthConsent.granted_at
              ? formatConsentDate(healthConsent.granted_at)
              : ""
          }`.trim()
        : "Не разрешено";

  // Подпись кнопки обязана совпадать с тем, что нажатие делает. Когда
  // состояние прочитать не удалось, кнопка повторяет чтение — обещать
  // «Разрешить» в этот момент значило бы врать о результате нажатия.
  const healthActionLabel = healthFailed
    ? "Повторить"
    : healthConsent?.granted
      ? "Отозвать"
      : "Разрешить";
  const healthActionAriaLabel = healthFailed
    ? "Повторить загрузку разрешения на данные о питании"
    : healthConsent?.granted
      ? "Отозвать разрешение учитывать питание"
      : "Разрешить учитывать питание";

  const onHealthSettled = useCallback((next: HealthConsentState) => {
    setHealthConsent(next);
    setToast({
      visible: true,
      message: next.granted
        ? "Хорошо, буду учитывать питание. Отозвать можно в профиле."
        : "Разрешение отозвано. Питание в разговоре не участвует.",
    });
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  // Functional setState pattern (adversarial CR P4 — race protection).
  // Stale-closure `setStatus({ ...status, ... })` could overwrite a
  // sibling toggle update if both writes are in flight; functional
  // form reads the freshest state at apply time.
  const onMarketingToggle = useCallback(async (next: boolean) => {
    setMarketingBusy(true);
    try {
      const updated = await setMarketingConsent(next);
      setStatus((s) =>
        s.kind === "ready" ? { ...s, consents: updated } : s,
      );
      setToast({
        visible: true,
        message: next
          ? "Хорошо, иногда буду показывать предложения от салонов."
          : "Понятно, предложений от салонов больше не будет.",
      });
    } catch {
      setToast({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setMarketingBusy(false);
    }
  }, []);

  return (
    <div className="profile-screen">
      {/* Skip-link renders only when R2 target is in the DOM (ready
          state). During loading / error the anchor doesn't exist, so
          activating the link is a no-op confusing keyboard users
          (adversarial CR P5). */}
      {status.kind === "ready" && (
        <a className="records-screen__skip-link" href="#profile-r2-anchor">
          К управлению приватностью
        </a>
      )}

      {/*
        Путь назад из клиентской поверхности. Без него владелец,
        переключившийся «посмотреть глазами клиента», остаётся здесь
        навсегда — то есть меняет одну ловушку на другую.

        Кнопка сама себя не рисует для человека с одной ролью:
        обычному клиенту переключать нечего.
      */}
      <SurfaceSwitchButton />

      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Профиль</h1>
      </header>

      {offline && (
        <div className="records-screen__offline-banner" role="status">
          Профиль может быть устаревшим. Проверь подключение.
        </div>
      )}

      <main className="profile-screen__main" id="profile-main">
        {status.kind === "loading" && <LoadingSkeleton />}
        {status.kind === "error" && (
          <div className="profile-screen__error">
            <StateError err={status.err} onRetry={load} />
          </div>
        )}
        {status.kind === "ready" && (
          <>
            {/* R1 — Header (реальный /customer/me, во всех сборках) */}
            <ProfileHeader me={status.me} />

            {/* R2 — Consent & Privacy */}
            <section
              className="profile-section"
              aria-labelledby="profile-r2-h2"
              id="profile-r2-anchor"
              tabIndex={-1}
            >
              <h2 id="profile-r2-h2" className="profile-section__heading">
                Согласия и приватность
              </h2>
              <dl className="profile-consent-list">
                <ConsentRow
                  variant="info"
                  title="Данные для записи"
                  statusText="Включено · нужно для записи"
                  description={
                    <>
                      Нужно, чтобы записывать тебя на услуги и показывать
                      мастеру детали визита.
                    </>
                  }
                />
                <ConsentRow
                  variant="info"
                  title="Данные для мастера"
                  statusText="Включено · нужно для проведения услуги"
                  description={
                    <>
                      Мастер видит: имя, услугу, время, заметку к записи
                      (если оставила). Мастер не видит память{" "}
                      <span lang="en">Ayla</span>, wellness-данные и личные
                      заметки.
                    </>
                  }
                />
                <ConsentRow
                  variant="toggle"
                  title="Акции и предложения"
                  ariaLabel="Получать акции и предложения от салонов"
                  checked={status.consents.marketing_consent}
                  busy={marketingBusy}
                  onChange={onMarketingToggle}
                  description={
                    <>
                      Иногда салоны делятся специальными предложениями. По
                      умолчанию выключено.
                    </>
                  }
                />
                {/* TODO(DRF-1520): строка «Хранение данных» скрыта — у
                    неё нет честных данных (реальный /me не отдаёт дату
                    общего согласия), а выдуманная дата — ложь о 152-ФЗ.
                    При возврате это будет НЕ тумблер и не строка с
                    датой, а сценарий с подтверждением последствий
                    отзыва согласия (потеря работы Ayla с профилем). */}
                {/* Медданные — реальный эндпоинт, видно во всех сборках.
                    Вариант "action", а не тумблер: особая категория по
                    152-ФЗ ст. 10 не переключается одним касанием мимо
                    текста согласия (см. HealthConsentSheet). */}
                <ConsentRow
                  variant="action"
                  title="Питание и здоровье"
                  statusText={healthStatusText}
                  actionLabel={healthActionLabel}
                  actionAriaLabel={healthActionAriaLabel}
                  busy={healthConsent === null && !healthFailed}
                  triggerRef={healthTriggerRef}
                  onAction={
                    healthFailed
                      ? loadHealthConsent
                      : () => setHealthSheetOpen(true)
                  }
                  description={
                    <>
                      Отдельное разрешение: данные о питании закон относит к
                      особой категории. Без него{" "}
                      <span lang="en">Ayla</span> не учитывает дневник питания
                      в разговоре. Отозвать можно в любой момент.
                    </>
                  }
                />
              </dl>
              <p className="profile-section__caption">
                Твои данные защищены. Здесь можно посмотреть, что хранится,
                скачать копию своих данных или удалить их — прямо в
                приложении.
              </p>
              <DisclosureSheet />
              <div className="profile-section__cta-row">
                <button
                  ref={exportTriggerRef}
                  type="button"
                  className="btn-secondary"
                  onClick={() => setExportOpen(true)}
                >
                  Запросить данные
                </button>
                <button
                  ref={deleteTriggerRef}
                  type="button"
                  className="btn-secondary profile-section__cta--cautious"
                  onClick={() => setDeleteOpen(true)}
                >
                  Удалить аккаунт
                </button>
              </div>
            </section>

            {/* R3 — Memory transparency (deferred) */}
            <section
              className="profile-section"
              aria-labelledby="profile-r3-h2"
            >
              <h2 id="profile-r3-h2" className="profile-section__heading">
                Что <span lang="en">Ayla</span> помнит
              </h2>
              <ComingSoonCard />
            </section>

            {/* TODO(DRF-1520): секция R4 «Подсказки от Ayla» скрыта —
                ручек me/proactive_opt_out пока нет, а тумблер, который
                человек двигает, а он ничего не делает, врёт про наличие
                контроля (решение владельца 05.09: «не показывать
                неработающие тумблеры»). Код fetchProactivePrefs /
                setProactiveOptOut в lib/customer-profile.ts сохранён —
                вернуть секцию, когда DRF-1520 даст реальные эндпоинты. */}

            {/* Cards (C7.2 skeleton) — real screen, honest empty state
                until the W3 passthrough ships. */}
            <section
              className="profile-section"
              aria-labelledby="profile-cards-h2"
            >
              <h2 id="profile-cards-h2" className="profile-section__heading">
                Оплата
              </h2>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => navigate("/customer/cards")}
              >
                Мои карты
              </button>
            </section>

            {/* R5 — Notifications (real prefs screen, #948) */}
            <NotificationCard
              onOpenSettings={() =>
                navigate("/customer/notification-settings")
              }
            />
          </>
        )}
      </main>

      {/* Bottom nav — mirror records / wellness so the «Я» tab is selected. */}
      <nav className="wellness-dash__nav" aria-label="Основная навигация">
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Главная"
          onClick={() => navigate("/customer/main")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            🏠
          </span>
          <span className="wellness-dash__nav-label">Главная</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="День"
          onClick={() => navigate("/customer/wellness")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            ☀
          </span>
          <span className="wellness-dash__nav-label">День</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Записи"
          onClick={() => navigate("/customer/records")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            📅
          </span>
          <span className="wellness-dash__nav-label">Записи</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Услуги"
          onClick={() => navigate("/customer/catalog")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            💅
          </span>
          <span className="wellness-dash__nav-label">Услуги</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab wellness-dash__nav-tab--active"
          aria-current="page"
          aria-label="Я"
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            👤
          </span>
          <span className="wellness-dash__nav-label">Я</span>
        </button>
      </nav>

      {/* C5 export sheet (152-ФЗ) */}
      <HealthConsentSheet
        open={healthSheetOpen}
        triggerRef={healthTriggerRef}
        onClose={() => setHealthSheetOpen(false)}
        granted={healthConsent?.granted ?? false}
        onSettled={onHealthSettled}
      />
      <PersonalDataExportSheet
        open={exportOpen}
        triggerRef={exportTriggerRef}
        onClose={() => setExportOpen(false)}
      />
      {/* C5 delete sheet (152-ФЗ) */}
      <PersonalDataDeleteSheet
        open={deleteOpen}
        triggerRef={deleteTriggerRef}
        onClose={() => setDeleteOpen(false)}
      />

      {/* Toggle confirmation toast (R6 §8.3 + marketing change). */}
      <Snackbar
        visible={toast.visible}
        message={toast.message}
        durationMs={4000}
        onTimeout={() => setToast(EMPTY_TOAST)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// R1 header card (sub-component kept in the same file — single use-site).
// ---------------------------------------------------------------------------

function ProfileHeader({ me }: { me: MeProfileResponse }) {
  const initials = avatarInitials(me.display_name);
  const tenants = me.tenant_names ?? [];
  const nearest = tenants[0] ?? "";
  const extra = Math.max(0, tenants.length - 1);
  const extraLabel = additionalSalonsLabel(extra);
  return (
    <section className="profile-header" aria-labelledby="profile-r1-h2">
      <h2 id="profile-r1-h2" className="visually-hidden">
        Профиль клиента
      </h2>
      <div className="profile-header__row">
        <div className="profile-header__avatar" aria-hidden="true">
          <span className="profile-header__initials">{initials}</span>
        </div>
        <div className="profile-header__text">
          <p className="profile-header__name">{me.display_name}</p>
          {/* Реальный /me не отдаёт MAX-хендл — строку не рисуем,
              пустой параграф был бы визуальным шумом. */}
          {me.max_handle && (
            <p className="profile-header__handle">{me.max_handle}</p>
          )}
          {nearest && (
            <p className="profile-header__scope">
              Клиент {nearest}
              {extraLabel ? ` ${extraLabel}` : ""}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton — matches the layout cadence (header card + 4 sections).
// Single shimmer surface respects `prefers-reduced-motion` via global CSS.
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="profile-screen__loading" aria-hidden="true">
      <div className="profile-header" aria-hidden="true">
        <div className="profile-header__row">
          <Skeleton width="56px" height="56px" radius="50%" />
          <div style={{ flex: 1 }}>
            <Skeleton width="50%" height="1.2em" />
            <div style={{ marginTop: "var(--s-1)" }}>
              <Skeleton width="35%" height="0.95em" />
            </div>
            <div style={{ marginTop: "var(--s-1)" }}>
              <Skeleton width="60%" height="0.9em" />
            </div>
          </div>
        </div>
      </div>
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="profile-section profile-section--skeleton">
          <Skeleton width="40%" height="1.1em" />
          <div style={{ marginTop: "var(--s-3)" }}>
            <Skeleton width="100%" height="0.95em" />
          </div>
          <div style={{ marginTop: "var(--s-1)" }}>
            <Skeleton width="80%" height="0.95em" />
          </div>
        </div>
      ))}
    </div>
  );
}
