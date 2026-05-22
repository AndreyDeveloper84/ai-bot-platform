/**
 * Master M7 notification settings screen — Bundle B / item 3 frontend.
 *
 * Route: /master/settings/notifications  (the §M7 spec quotes a bare
 * /settings/notifications path; the customer/admin/master SPA splits
 * surfaces by /master/* /admin/* prefixes — we keep that convention).
 *
 * Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M7
 * (lines 777-843). Verbatim Russian copy throughout — paraphrasing is
 * banned per the bundle brief.
 *
 * Spec quote (§M7 lines 803-805, forced-ON urgent toggle):
 *
 *     «Срочно (HUMAN_LOCKED) … [Switch: ON] (нельзя выключить) … Safety
 *     — forced ON»
 *
 * Spec quote (§M7 lines 815-817, quiet-hours helper):
 *
 *     «Тихие часы … с 21:00 до 09:00 … [Изменить] … В тихом режиме
 *     помощник напишет утром перед сменой.»
 *
 * Spec quote (§M7 line 841-842, error + all-disabled states):
 *
 *     «Save error: «Не удалось сохранить. Попробуйте ещё раз.» … All
 *     disabled: warning banner «Вы отключили все уведомления. Карина
 *     может попросить включить хотя бы срочные.»»
 *
 * Toggle semantics: every switch tap performs an optimistic UI flip
 * and PATCHes the single field. On success → subtle haptic
 * (selectionChanged) — switches are rapid + per-row. On error → revert
 * + inline toast (no haptic for switch errors since the revert + toast
 * already telegraphs failure). The «Срочно» toggle is DISABLED with
 * `aria-disabled="true"` so taps never reach the network; defensive
 * 400 ``urgent_forced_on`` revert is still wired in case the backend
 * ever rejects a future writer that bypasses the disabled UI.
 *
 * Quiet-hours editor modal:
 *   - Two ``<input type="time">`` (start, end). HTML-native picker —
 *     no JS time-picker dep.
 *   - Validate ``start !== end`` client-side (backend also enforces;
 *     400 ``time_invalid``). ``start > end`` is LEGAL — overnight
 *     window per the §M7 default 21:00 → 09:00.
 *   - [Сохранить] PATCHes ``{quiet_start, quiet_end}`` together;
 *     success haptic + toast.
 *   - [Отмена] discards.
 *   - ``setClosingConfirmation(true)`` while the modal is dirty +
 *     unsaved — guards against accidental MAX close swallowing edits.
 *
 * All-disabled warning banner predicate (§842):
 *
 *     ``!new_booking && !booking_change && !personal_message &&
 *       !morning_brief && !evening_summary``
 *
 *   The «Срочно» switch is forced ON so it's not in the predicate;
 *   quiet-hours is orthogonal (it gates DELIVERY of the toggled
 *   categories, not WHETHER they're enabled). Predicate updates
 *   reactively after every successful toggle.
 *
 * Bridge API (§M7 lines 836-837 — implied baseline + master conventions):
 *   - BackButton.show() → /master/profile (entry-point per §M7 line 780)
 *   - HapticFeedback.selectionChanged() on switch toggle save
 *   - HapticFeedback.notificationOccurred('success') on quiet-hours save
 *   - HapticFeedback.notificationOccurred('error') on quiet-hours save error
 *   - enableClosingConfirmation() while the quiet-hours editor is dirty
 *
 * State matrix coverage:
 *   - Loading: skeleton switch rows
 *   - Save error per toggle: revert + inline toast
 *   - All-toggle-disabled: warning banner at top
 *   - Quiet hours editor with start==end: inline error «Время начала
 *     и конца должны отличаться»
 *   - Network offline: banner (mirrors MasterProfileScreen pattern)
 *   - Initial GET creates defaults transparently — no «first load» banner
 *
 * Deferred (out of scope this PR, documented in PR body):
 *   - TZ-aware quiet hours (backend stores naive HH:MM in tenant TZ)
 *   - MAX bot subscription DB write — MAX has no push beyond chat anyway
 *   - Per-customer notification overrides
 *   - Email / SMS channel split
 *   - Vitest — manual test plan in PR body
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { MasterTabBar } from "../components/MasterTabBar";
import { Snackbar } from "../components/Snackbar";
import { ApiError } from "../lib/api";
import {
  getNotificationPrefs,
  patchNotificationPrefs,
  type MasterNotificationPrefs,
  type NotificationPrefsErrorSlug,
  type NotificationPrefsPatch,
} from "../lib/master-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../lib/max-sdk";

// --- Russian copy (VERBATIM from §M7) -----------------------------------

const COPY = {
  header: "Уведомления",
  sections: {
    workHours: "━━ В РАБОЧЕЕ ВРЕМЯ ━━━━━━━━━━━",
    quietMode: "━━ ТИХИЙ РЕЖИМ ━━━━━━━━━━━━━━",
    dailySummaries: "━━ ЕЖЕДНЕВНЫЕ СВОДКИ ━━━━━━━━",
  },
  rows: {
    newBooking: {
      title: "Новая запись",
      subtitle: "Помощник записал клиента к вам",
    },
    bookingChange: {
      title: "Изменение записи",
      subtitle: "Клиент перенёс или отменил",
    },
    personalMessage: {
      title: "Личное сообщение клиента",
      subtitle: "Клиент написал что-то лично вам",
    },
    urgent: {
      title: "Срочно (HUMAN_LOCKED)",
      subtitle: "Только если ситуация касается вас",
      lockTooltip: "Безопасность — нельзя выключить",
    },
    quietHoursEnabled: {
      title: "Не беспокоить вне работы",
    },
    quietHoursRow: {
      title: "Тихие часы",
      helper: "В тихом режиме помощник напишет утром перед сменой.",
      edit: "Изменить",
    },
    morningBrief: {
      title: "Утренний бриф (08:30)",
      subtitle: "Расписание на день + сюрпризы",
    },
    eveningSummary: {
      title: "Вечерний итог (после смены)",
      subtitle: "Сколько провели, что завтра",
    },
  },
  banners: {
    allDisabled:
      "Вы отключили все уведомления. Карина может попросить включить хотя бы срочные.",
    offline: "Нет связи. Попробуйте ещё раз позже.",
  },
  toasts: {
    saveError: "Не удалось сохранить. Попробуйте ещё раз.",
    saved: "✓ Сохранено",
  },
  modal: {
    title: "Тихие часы",
    startLabel: "Начало",
    endLabel: "Конец",
    sameTimeError: "Время начала и конца должны отличаться",
    overnightHint:
      "Подсказка: можно задать ночной интервал, например, с 23:00 до 07:00.",
    save: "Сохранить",
    cancel: "Отмена",
  },
  states: {
    loading: "Загружаем настройки…",
    errorTitle: "Не получилось загрузить",
    errorBody:
      "Не получилось загрузить настройки уведомлений. Проверьте интернет.",
    retry: "Попробовать снова",
  },
};

// --- State model --------------------------------------------------------

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; prefs: MasterNotificationPrefs }
  | { kind: "error"; err: unknown };

interface QuietHoursDraft {
  start: string; // HH:MM
  end: string; // HH:MM
  saving: boolean;
  err: string;
}

// --- Helpers ------------------------------------------------------------

/** §842 predicate — all togglable categories OFF (urgent is forced ON, excluded). */
function isAllDisabled(p: MasterNotificationPrefs): boolean {
  return (
    !p.new_booking &&
    !p.booking_change &&
    !p.personal_message &&
    !p.morning_brief &&
    !p.evening_summary
  );
}

// --- Component ----------------------------------------------------------

export function MasterNotificationSettingsScreen() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [editor, setEditor] = useState<QuietHoursDraft | null>(null);
  const [toast, setToast] = useState("");
  const [offlineBanner, setOfflineBanner] = useState(false);
  // Track which row is mid-save so we can render a subtle pending hint
  // and disable the same row to dodge a re-tap mid-flight.
  const [pendingField, setPendingField] =
    useState<keyof NotificationPrefsPatch | null>(null);

  // --- Bridge: BackButton + closing-confirmation ---
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/master/profile");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Modal-dirty closing-confirmation: ON while quiet-hours editor is open
  // and at least one value differs from the persisted prefs.
  const editorDirty = useMemo(() => {
    if (!editor || phase.kind !== "ready") return false;
    return (
      editor.start !== phase.prefs.quiet_start ||
      editor.end !== phase.prefs.quiet_end
    );
  }, [editor, phase]);

  useEffect(() => {
    setClosingConfirmation(editorDirty);
  }, [editorDirty]);

  // --- Initial load ---
  const fetchPrefs = useCallback(async () => {
    setPhase({ kind: "loading" });
    setOfflineBanner(false);
    try {
      const prefs = await getNotificationPrefs();
      setPhase({ kind: "ready", prefs });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void fetchPrefs();
  }, [fetchPrefs]);

  // --- Toggle handler with optimistic UI + revert on error ---
  const toggle = useCallback(
    async (field: keyof NotificationPrefsPatch, nextValue: boolean) => {
      if (phase.kind !== "ready") return;
      if (pendingField) return; // ignore rapid re-tap while saving

      const before = phase.prefs;
      // Optimistic flip
      const optimistic: MasterNotificationPrefs = {
        ...before,
        [field]: nextValue,
      };
      setPhase({ kind: "ready", prefs: optimistic });
      setPendingField(field);
      setOfflineBanner(false);

      try {
        const saved = await patchNotificationPrefs({
          [field]: nextValue,
        } as NotificationPrefsPatch);
        // Backend returns the canonical row — use it to overwrite the
        // optimistic state (in case the backend normalised something).
        setPhase({ kind: "ready", prefs: saved });
        hapticSelection();
      } catch (e) {
        // Revert.
        setPhase({ kind: "ready", prefs: before });
        if (e instanceof ApiError) {
          // urgent_forced_on should not happen — switch is disabled. Log
          // defensively so we notice if a future writer bypasses the UI.
          const slug = e.slug as NotificationPrefsErrorSlug;
          if (slug === "urgent_forced_on") {
            // eslint-disable-next-line no-console
            console.warn(
              "[M7] backend rejected urgent=false — UI guard breached?",
              e.detail,
            );
          }
          setToast(COPY.toasts.saveError);
        } else {
          setOfflineBanner(true);
          setToast(COPY.toasts.saveError);
        }
      } finally {
        setPendingField(null);
      }
    },
    [phase, pendingField],
  );

  // --- Quiet-hours editor lifecycle ---
  const openQuietEditor = useCallback(() => {
    if (phase.kind !== "ready") return;
    hapticSelection();
    setEditor({
      start: phase.prefs.quiet_start,
      end: phase.prefs.quiet_end,
      saving: false,
      err: "",
    });
  }, [phase]);

  const cancelQuietEditor = useCallback(() => {
    setEditor(null);
  }, []);

  const saveQuietEditor = useCallback(async () => {
    if (!editor || phase.kind !== "ready") return;
    if (editor.start === editor.end) {
      setEditor({ ...editor, err: COPY.modal.sameTimeError });
      hapticNotify("error");
      return;
    }
    setEditor({ ...editor, saving: true, err: "" });
    setOfflineBanner(false);
    try {
      const saved = await patchNotificationPrefs({
        quiet_start: editor.start,
        quiet_end: editor.end,
      });
      setPhase({ kind: "ready", prefs: saved });
      setEditor(null);
      hapticNotify("success");
      setToast(COPY.toasts.saved);
    } catch (e) {
      if (e instanceof ApiError) {
        const slug = e.slug as NotificationPrefsErrorSlug;
        const msg =
          slug === "time_invalid"
            ? COPY.modal.sameTimeError
            : e.detail || COPY.toasts.saveError;
        setEditor({ ...editor, saving: false, err: msg });
      } else {
        setOfflineBanner(true);
        setEditor({ ...editor, saving: false, err: "" });
      }
      hapticNotify("error");
    }
  }, [editor, phase]);

  // --- Render branches ---
  if (phase.kind === "loading") {
    return (
      <NotifFrame>
        <LoadingSkeleton />
      </NotifFrame>
    );
  }
  if (phase.kind === "error") {
    return (
      <NotifFrame>
        <ErrorPane onRetry={() => void fetchPrefs()} />
      </NotifFrame>
    );
  }

  const prefs = phase.prefs;
  const allDisabled = isAllDisabled(prefs);

  return (
    <NotifFrame>
      {offlineBanner ? <OfflineBanner /> : null}
      {allDisabled ? <AllDisabledBanner /> : null}

      <NotifSection title={COPY.sections.workHours}>
        <SwitchRow
          title={COPY.rows.newBooking.title}
          subtitle={COPY.rows.newBooking.subtitle}
          checked={prefs.new_booking}
          onChange={(v) => void toggle("new_booking", v)}
          pending={pendingField === "new_booking"}
        />
        <SwitchRow
          title={COPY.rows.bookingChange.title}
          subtitle={COPY.rows.bookingChange.subtitle}
          checked={prefs.booking_change}
          onChange={(v) => void toggle("booking_change", v)}
          pending={pendingField === "booking_change"}
        />
        <SwitchRow
          title={COPY.rows.personalMessage.title}
          subtitle={COPY.rows.personalMessage.subtitle}
          checked={prefs.personal_message}
          onChange={(v) => void toggle("personal_message", v)}
          pending={pendingField === "personal_message"}
        />
        <SwitchRow
          title={COPY.rows.urgent.title}
          subtitle={COPY.rows.urgent.subtitle}
          checked={prefs.urgent}
          onChange={() => {
            // Defensive — switch is disabled, this should never run.
            hapticNotify("error");
          }}
          disabled
          locked
          lockTooltip={COPY.rows.urgent.lockTooltip}
        />
      </NotifSection>

      <NotifSection title={COPY.sections.quietMode}>
        <SwitchRow
          title={COPY.rows.quietHoursEnabled.title}
          checked={prefs.quiet_hours_enabled}
          onChange={(v) => void toggle("quiet_hours_enabled", v)}
          pending={pendingField === "quiet_hours_enabled"}
        />
        {prefs.quiet_hours_enabled ? (
          <div className="m-notif__quiet-block">
            <div className="m-notif__quiet-row">
              <div className="m-notif__row-text">
                <div className="m-notif__row-title">
                  {COPY.rows.quietHoursRow.title}
                </div>
                <div className="m-notif__row-subtitle">
                  с {prefs.quiet_start} до {prefs.quiet_end}
                </div>
              </div>
              <button
                type="button"
                className="btn-secondary m-notif__edit-btn"
                onClick={openQuietEditor}
              >
                {COPY.rows.quietHoursRow.edit}
              </button>
            </div>
            <p className="m-notif__helper">
              {COPY.rows.quietHoursRow.helper}
            </p>
          </div>
        ) : null}
      </NotifSection>

      <NotifSection title={COPY.sections.dailySummaries}>
        <SwitchRow
          title={COPY.rows.morningBrief.title}
          subtitle={COPY.rows.morningBrief.subtitle}
          checked={prefs.morning_brief}
          onChange={(v) => void toggle("morning_brief", v)}
          pending={pendingField === "morning_brief"}
        />
        <SwitchRow
          title={COPY.rows.eveningSummary.title}
          subtitle={COPY.rows.eveningSummary.subtitle}
          checked={prefs.evening_summary}
          onChange={(v) => void toggle("evening_summary", v)}
          pending={pendingField === "evening_summary"}
        />
      </NotifSection>

      <MasterTabBar
        unreadCount={0}
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />

      {editor !== null ? (
        <QuietHoursEditor
          state={editor}
          onChangeStart={(v) =>
            setEditor((curr) =>
              curr ? { ...curr, start: v, err: "" } : curr,
            )
          }
          onChangeEnd={(v) =>
            setEditor((curr) =>
              curr ? { ...curr, end: v, err: "" } : curr,
            )
          }
          onCancel={cancelQuietEditor}
          onSave={() => void saveQuietEditor()}
        />
      ) : null}

      <Snackbar
        visible={toast.length > 0}
        message={toast}
        durationMs={2400}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />
    </NotifFrame>
  );
}

// --- Sub-components -----------------------------------------------------

function NotifFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="m-notif">
      <header className="m-notif__header">
        <h1 className="m-notif__title">{COPY.header}</h1>
      </header>
      {children}
    </div>
  );
}

function NotifSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="m-notif__section">
      <h2 className="m-notif__section-title">{title}</h2>
      {children}
    </section>
  );
}

interface SwitchRowProps {
  title: string;
  subtitle?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  locked?: boolean;
  lockTooltip?: string;
  pending?: boolean;
}

function SwitchRow({
  title,
  subtitle,
  checked,
  onChange,
  disabled = false,
  locked = false,
  lockTooltip,
  pending = false,
}: SwitchRowProps) {
  return (
    <div
      className={
        disabled
          ? "m-notif__switch-row m-notif__switch-row--disabled"
          : "m-notif__switch-row"
      }
    >
      <div className="m-notif__row-text">
        <div className="m-notif__row-title">
          {title}
          {locked ? (
            <span
              className="m-notif__lock"
              role="img"
              aria-label={lockTooltip ?? "Заблокировано"}
              title={lockTooltip}
            >
              {" "}
              🔒
            </span>
          ) : null}
        </div>
        {subtitle ? (
          <div className="m-notif__row-subtitle">{subtitle}</div>
        ) : null}
      </div>
      <label
        className={
          disabled
            ? "m-notif__switch m-notif__switch--disabled"
            : "m-notif__switch"
        }
        title={locked ? lockTooltip : undefined}
        aria-disabled={disabled || pending ? "true" : "false"}
      >
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled || pending}
          onChange={(e) => onChange(e.target.checked)}
          aria-label={title}
        />
        <span className="m-notif__switch-track">
          <span className="m-notif__switch-thumb" />
        </span>
      </label>
    </div>
  );
}

function QuietHoursEditor({
  state,
  onChangeStart,
  onChangeEnd,
  onCancel,
  onSave,
}: {
  state: QuietHoursDraft;
  onChangeStart: (v: string) => void;
  onChangeEnd: (v: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div className="m-notif__sheet" role="dialog" aria-modal="true">
      <div className="m-notif__sheet-card">
        <h3 className="m-notif__sheet-title">{COPY.modal.title}</h3>
        <div className="m-notif__time-row">
          <label className="m-notif__time-field">
            <span className="m-notif__time-label">
              {COPY.modal.startLabel}
            </span>
            <input
              type="time"
              value={state.start}
              onChange={(e) => onChangeStart(e.target.value)}
              disabled={state.saving}
              aria-label={COPY.modal.startLabel}
            />
          </label>
          <label className="m-notif__time-field">
            <span className="m-notif__time-label">
              {COPY.modal.endLabel}
            </span>
            <input
              type="time"
              value={state.end}
              onChange={(e) => onChangeEnd(e.target.value)}
              disabled={state.saving}
              aria-label={COPY.modal.endLabel}
            />
          </label>
        </div>
        <p className="m-notif__overnight-hint">{COPY.modal.overnightHint}</p>
        {state.err ? (
          <p className="m-notif__error" role="alert">
            {state.err}
          </p>
        ) : null}
        <div className="m-notif__sheet-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={state.saving}
          >
            {COPY.modal.cancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSave}
            disabled={state.saving || state.start === state.end}
          >
            {state.saving ? "Сохраняем…" : COPY.modal.save}
          </button>
        </div>
      </div>
    </div>
  );
}

function AllDisabledBanner() {
  return (
    <div
      className="callout m-notif__banner-warning"
      role="status"
      aria-live="polite"
    >
      <p style={{ margin: 0 }}>{COPY.banners.allDisabled}</p>
    </div>
  );
}

function OfflineBanner() {
  return (
    <div
      className="callout callout--danger"
      role="alert"
      style={{ margin: "var(--s-2) var(--s-3)" }}
    >
      <p style={{ margin: 0 }}>{COPY.banners.offline}</p>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="m-notif__skeleton-wrap" aria-busy="true">
      <p className="m-notif__loading-label">{COPY.states.loading}</p>
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="m-card m-card--skel">
          <div
            className="skeleton"
            style={{ width: "55%", height: "1em" }}
          />
          <div
            className="skeleton"
            style={{ width: "75%", height: "0.85em", marginTop: 6 }}
          />
        </div>
      ))}
    </div>
  );
}

function ErrorPane({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="m-notif__section">
      <h2 className="m-notif__section-title">{COPY.states.errorTitle}</h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.states.errorBody}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {COPY.states.retry}
          </button>
        </div>
      </div>
    </section>
  );
}
