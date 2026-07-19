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
 *   R1 — header (avatar initials fallback + name + handle + scope)
 *   R2 — consent: 3 locked rows + marketing toggle + §4.2 accordion
 *        + «Запросить данные» / «Удалить аккаунт» → C5 sheets
 *        (PersonalDataSheets.tsx; support deeplink = error fallback)
 *   R3 — memory transparency: coming-soon card (no data, no clear)
 *   R4 — proactive AI toggle + transactional-always note
 *   R5 — notifications: MAX channel + soft timing + entry → support
 *   R6 — states: loading skeleton / API-down with retry / offline
 *        banner / proactive-off explainer snackbar
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
  PersonalDataDeleteSheet,
  PersonalDataExportSheet,
} from "../components/PersonalDataSheets";
import { Skeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import type { SupportPreset } from "../components/SupportEntrySheet";
import { ToggleSwitch } from "../components/ToggleSwitch";
import {
  additionalSalonsLabel,
  avatarInitials,
  fetchConsents,
  fetchMe,
  fetchProactivePrefs,
  formatConsentDate,
  setMarketingConsent,
  setProactiveOptOut,
  type ConsentsResponse,
  type MeProfileResponse,
  type ProactivePrefsResponse,
} from "../lib/customer-profile";
import { STUB_SURFACES_ENABLED } from "../lib/feature-flags";

// ---------------------------------------------------------------------------
// Stub-backed sections flag (pilot phase 2a, commit 3). R1 identity
// header, R2 consent rows + marketing toggle and R4 proactive toggle
// read DEV-only stubs (`customer-profile.ts`) whose prod guard throws
// BY DESIGN (`StubNotWiredError` — the 152-ФЗ truthfulness gate: no
// fake identity / fake consent date in prod). Until the backing
// endpoints ship (post-pilot backlog, W3), those sections render only
// when STUB_SURFACES_ENABLED (lib/feature-flags.ts — the same gate as
// the wellness / catalog stub surfaces): hidden UI is more honest than
// a crashing screen.
// Everything else — C5 export/delete (152-ФЗ), R3 memory coming-soon,
// R5 notifications via support — shows in all builds.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Loading / error / offline state machine.
// ---------------------------------------------------------------------------

type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | {
      kind: "ready";
      /** Null in prod builds — stub-backed sections stay hidden. */
      me: MeProfileResponse | null;
      consents: ConsentsResponse | null;
      proactive: ProactivePrefsResponse | null;
    };

interface ToastState {
  visible: boolean;
  message: string;
}

const EMPTY_TOAST: ToastState = { visible: false, message: "" };

export function CustomerProfileScreen() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [toast, setToast] = useState<ToastState>(EMPTY_TOAST);
  const [marketingBusy, setMarketingBusy] = useState(false);
  const [proactiveBusy, setProactiveBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [notificationsSheet, setNotificationsSheet] =
    useState<SupportPreset | null>(null);
  const exportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    if (!STUB_SURFACES_ENABLED) {
      // Prod build: stub-backed endpoints don't exist — skip the fetches
      // entirely (the stub lib would throw by design) and render only
      // the real sections below.
      setStatus({ kind: "ready", me: null, consents: null, proactive: null });
      return;
    }
    setStatus({ kind: "loading" });
    try {
      const [me, consents, proactive] = await Promise.all([
        fetchMe(),
        fetchConsents(),
        fetchProactivePrefs(),
      ]);
      setStatus({ kind: "ready", me, consents, proactive });
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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

  const onProactiveToggle = useCallback(async (next: boolean) => {
    // Toggle ON = proactive enabled = opt_out false.
    const optOut = !next;
    setProactiveBusy(true);
    try {
      const updated = await setProactiveOptOut(optOut);
      setStatus((s) =>
        s.kind === "ready" ? { ...s, proactive: updated } : s,
      );
        if (optOut) {
          // Spec §8.3 verbatim (three sentences — adversarial CR P2:
          // dropping the «не буду писать первой» line confuses the
          // customer about whether transactional reminders still
          // arrive).
          setToast({
            visible: true,
            message:
              "Проактивные подсказки выключены. Я не буду писать первой с рекомендациями. Важные сообщения по записям всё равно будут приходить (подтверждения, переносы, отмены).",
          });
        } else {
          setToast({
            visible: true,
            message: "Подсказки от Ayla включены.",
          });
        }
    } catch {
      setToast({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setProactiveBusy(false);
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

      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate(-1)}
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
            {/* R1 — Header (stub-backed: DEV builds only) */}
            {status.me && <ProfileHeader me={status.me} />}

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
              {/* Consent rows — stub-backed, DEV builds only. */}
              {status.consents && (
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
                <ConsentRow
                  variant="info"
                  title="Хранение данных"
                  statusText={`Согласие дано ${formatConsentDate(
                    status.consents.data_storage_consent_at,
                  )}`}
                  description={
                    <>
                      Полный отзыв согласия означает, что{" "}
                      <span lang="en">Ayla</span> больше не сможет работать
                      с твоим профилем. Для этого можно удалить свои данные
                      кнопкой ниже.
                    </>
                  }
                />
              </dl>
              )}
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

            {/* R4 — Proactive AI (stub-backed: DEV builds only) */}
            {status.proactive && (
            <section
              className="profile-section"
              aria-labelledby="profile-r4-h2"
            >
              <h2 id="profile-r4-h2" className="profile-section__heading">
                Подсказки от <span lang="en">Ayla</span>
              </h2>
              <div className="profile-proactive">
                <div className="profile-proactive__row">
                  <span className="profile-proactive__label">
                    Получать подсказки от Ayla
                  </span>
                  <ToggleSwitch
                    checked={!status.proactive.proactive_messages_opt_out}
                    onChange={onProactiveToggle}
                    ariaLabel="Получать подсказки от Ayla"
                    disabled={proactiveBusy}
                  />
                </div>
                <p className="profile-proactive__note">
                  Я буду писать первой — наблюдения, рекомендации, идеи.
                  Транзакционные напоминания (подтверждения записей,
                  переносы, отмены) приходят всегда.
                </p>
              </div>
            </section>
            )}

            {/* R5 — Notifications */}
            <NotificationCard
              supportPreset={notificationsSheet}
              onOpenSupport={() => setNotificationsSheet("notifications")}
              onCloseSupport={() => setNotificationsSheet(null)}
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
          onClick={() => navigate("/")}
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
          <p className="profile-header__handle">{me.max_handle}</p>
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
