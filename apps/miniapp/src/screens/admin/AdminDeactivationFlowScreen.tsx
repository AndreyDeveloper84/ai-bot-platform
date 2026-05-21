/**
 * Admin MM5 deactivation flow — 4-step state machine.
 *
 * Route: /admin/team/:masterId/deactivate
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM5
 * (lines 679-852). Verbatim Russian copy throughout.
 *
 * Spec quote (§MM5, lines 693-697):
 *
 *     «Анна станет неактивна — войти в систему не сможет, новых
 *      записей помощник к ней не предложит. Все данные сохранятся.
 *      Можно вернуть в любой момент одним кликом.»
 *
 * Steps:
 *   1. Confirm intent + bookings inventory (calls /deactivation-preview)
 *   2. Per-booking reassignment / cancellation
 *   3. Final confirm + notification template preview
 *   4. Result screen (calls /deactivate, then displays summary)
 *
 * Step 2 is skipped when the preview returns 0 future bookings.
 *
 * Owner-only guard: the destructive POST /deactivate is gated to
 * is_owner on the backend. We mirror that on Step 3 — Admin sees the
 * destructive button disabled with a tooltip. Receptionist cannot
 * route here at all (no entry point — they see the action sheet
 * button disabled in MM1).
 *
 * 409 race: if a new booking arrives between Step 1 preview and Step
 * 3 execute, the backend returns 409 plan_stale_refresh_required.
 * The handler resets state, re-fetches preview, returns to Step 1
 * with a toast.
 *
 * Bridge API:
 *   - BackButton.show() on Steps 2 + 3
 *   - HapticFeedback.impactOccurred('heavy') on destructive tap
 *   - enableClosingConfirmation() while a plan is dirty
 *   - HapticFeedback.notificationOccurred('success') on Step 4
 */

import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Snackbar } from "../../components/Snackbar";
import { StateError } from "../../components/StateError";
import { ApiError } from "../../lib/api";
import {
  executeDeactivation,
  previewDeactivation,
  type BookingActionEntry,
  type BookingActionKind,
  type DeactivationFutureBooking,
  type DeactivationPreview,
  type DeactivationResult,
  type MeResponse,
} from "../../lib/admin-api";
import { formatDateLong, formatTimeHM } from "../../lib/masterDateFormat";
import {
  hapticImpact,
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../../lib/max-sdk";

interface Props {
  me: MeResponse;
}

type Step = 1 | 2 | 3 | 4;

interface BookingDecision {
  action: BookingActionKind;
  to_master_id?: string;
}

interface FlowState {
  step: Step;
  preview: DeactivationPreview | null;
  decisions: Record<string, BookingDecision>;
  notifyReassignedMasters: boolean;
  customTemplate: string;
  reason: string;
  loadingPreview: boolean;
  previewErr: unknown;
  submitting: boolean;
  submitErr: unknown;
  result: DeactivationResult | null;
  toast: string;
}

const initialState: FlowState = {
  step: 1,
  preview: null,
  decisions: {},
  notifyReassignedMasters: true,
  customTemplate: "",
  reason: "",
  loadingPreview: false,
  previewErr: null,
  submitting: false,
  submitErr: null,
  result: null,
  toast: "",
};

type Action =
  | { type: "preview/start" }
  | { type: "preview/ok"; preview: DeactivationPreview }
  | { type: "preview/err"; err: unknown }
  | { type: "step/set"; step: Step }
  | { type: "decision/set"; bookingId: string; decision: BookingDecision }
  | { type: "notify/toggle"; value: boolean }
  | { type: "template/set"; value: string }
  | { type: "reason/set"; value: string }
  | { type: "submit/start" }
  | { type: "submit/ok"; result: DeactivationResult }
  | { type: "submit/err"; err: unknown }
  | { type: "toast"; value: string }
  | { type: "reset/race"; preview: DeactivationPreview };

function reducer(state: FlowState, action: Action): FlowState {
  switch (action.type) {
    case "preview/start":
      return { ...state, loadingPreview: true, previewErr: null };
    case "preview/ok":
      return {
        ...state,
        loadingPreview: false,
        preview: action.preview,
        previewErr: null,
      };
    case "preview/err":
      return { ...state, loadingPreview: false, previewErr: action.err };
    case "step/set":
      return { ...state, step: action.step };
    case "decision/set":
      return {
        ...state,
        decisions: {
          ...state.decisions,
          [action.bookingId]: action.decision,
        },
      };
    case "notify/toggle":
      return { ...state, notifyReassignedMasters: action.value };
    case "template/set":
      return { ...state, customTemplate: action.value };
    case "reason/set":
      return { ...state, reason: action.value };
    case "submit/start":
      return { ...state, submitting: true, submitErr: null };
    case "submit/ok":
      return {
        ...state,
        submitting: false,
        submitErr: null,
        result: action.result,
        step: 4,
      };
    case "submit/err":
      return { ...state, submitting: false, submitErr: action.err };
    case "toast":
      return { ...state, toast: action.value };
    case "reset/race":
      return {
        ...state,
        preview: action.preview,
        decisions: {},
        step: 1,
        toast: "Появилась новая запись — список обновлён",
        submitting: false,
        submitErr: null,
      };
    default:
      return state;
  }
}

function firstName(fullName: string): string {
  const parts = (fullName || "").trim().split(/\s+/);
  return parts[0] ?? fullName;
}

function bookingDateHuman(visitAt: string | null): string {
  if (!visitAt) return "—";
  // «21 мая 14:30»
  const dateLong = formatDateLong(visitAt); // «Среда, 21 мая»
  const time = formatTimeHM(visitAt); // «14:30»
  // Strip weekday — spec example reads «21 мая 14:30».
  const noWeekday = dateLong.includes(",")
    ? dateLong.split(", ")[1] ?? dateLong
    : dateLong;
  return `${noWeekday} ${time}`;
}

export function AdminDeactivationFlowScreen({ me }: Props) {
  const navigate = useNavigate();
  const { masterId = "" } = useParams<{ masterId: string }>();
  const [state, dispatch] = useReducer(reducer, initialState);
  const [showTemplateEdit, setShowTemplateEdit] = useState<boolean>(false);

  const ownerOnlyDisabledLabel = "Только владелец может деактивировать мастера";

  // Initial preview fetch.
  const fetchPreview = useCallback(
    async (asRaceRefresh = false) => {
      dispatch({ type: "preview/start" });
      try {
        const preview = await previewDeactivation(masterId);
        if (asRaceRefresh) {
          dispatch({ type: "reset/race", preview });
        } else {
          dispatch({ type: "preview/ok", preview });
        }
      } catch (e) {
        dispatch({ type: "preview/err", err: e });
      }
    },
    [masterId],
  );

  useEffect(() => {
    if (!masterId) return;
    void fetchPreview();
  }, [fetchPreview, masterId]);

  // Bridge — BackButton on Steps 2 + 3; closing confirmation while dirty.
  useEffect(() => {
    const shouldShowBack = state.step === 2 || state.step === 3;
    setBackButton(shouldShowBack);
    const hasDirtyState =
      state.step === 2 ||
      state.step === 3 ||
      Object.keys(state.decisions).length > 0;
    setClosingConfirmation(hasDirtyState && state.step !== 4);
    if (!shouldShowBack) return undefined;
    const off = onBackButton(() => {
      hapticSelection();
      dispatch({
        type: "step/set",
        step: (state.step === 3 ? 2 : 1) as Step,
      });
    });
    return () => {
      off();
    };
  }, [state.step, state.decisions]);

  // Cleanup on unmount — drop closing confirmation + BackButton.
  useEffect(() => {
    return () => {
      setBackButton(false);
      setClosingConfirmation(false);
    };
  }, []);

  // --- derived ----------------------------------------------------------

  const preview = state.preview;
  const futureBookings: DeactivationFutureBooking[] = preview?.future_bookings ?? [];
  const hasFutureBookings = futureBookings.length > 0;

  const reassignCount = useMemo(
    () =>
      Object.values(state.decisions).filter((d) => d.action === "reassign")
        .length,
    [state.decisions],
  );
  const cancelCount = useMemo(
    () => Object.values(state.decisions).filter((d) => d.action === "cancel").length,
    [state.decisions],
  );
  const undecidedCount = useMemo(
    () => futureBookings.length - (reassignCount + cancelCount),
    [futureBookings.length, reassignCount, cancelCount],
  );

  const reassignTargetNames = useMemo(() => {
    const names = new Set<string>();
    for (const booking of futureBookings) {
      const dec = state.decisions[booking.booking_id];
      if (dec?.action === "reassign" && dec.to_master_id) {
        const target = booking.fallback_masters.find(
          (fm) => fm.master_id === dec.to_master_id,
        );
        if (target) names.add(firstName(target.name));
      }
    }
    return Array.from(names);
  }, [futureBookings, state.decisions]);

  // --- actions ----------------------------------------------------------

  const handleStep1Continue = useCallback(() => {
    hapticSelection();
    if (hasFutureBookings) {
      dispatch({ type: "step/set", step: 2 });
    } else {
      dispatch({ type: "step/set", step: 3 });
    }
  }, [hasFutureBookings]);

  const handleStep2Continue = useCallback(() => {
    if (undecidedCount > 0) return;
    hapticSelection();
    dispatch({ type: "step/set", step: 3 });
  }, [undecidedCount]);

  const handleSubmit = useCallback(async () => {
    if (!preview) return;
    if (!me.is_owner) return; // UI button is disabled; defence-in-depth.
    hapticImpact("heavy");
    const plan: BookingActionEntry[] = futureBookings.map((b) => {
      const dec = state.decisions[b.booking_id];
      if (!dec) {
        // Default unmade decisions to cancel — should not be reachable
        // because the Step 2 footer blocks Continue while undecided
        // exists. Cancel is the safer fallback than reassign.
        return { booking_id: b.booking_id, action: "cancel" };
      }
      if (dec.action === "reassign" && dec.to_master_id) {
        return {
          booking_id: b.booking_id,
          action: "reassign",
          to_master_id: dec.to_master_id,
        };
      }
      return { booking_id: b.booking_id, action: "cancel" };
    });

    dispatch({ type: "submit/start" });
    try {
      const result = await executeDeactivation(masterId, {
        bookings_plan: plan,
        reason: state.reason || undefined,
        notify_reassigned_masters: state.notifyReassignedMasters,
        custom_notification_template: state.customTemplate || null,
      });
      hapticNotify("success");
      dispatch({ type: "submit/ok", result });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Race condition — backend says preview is stale. Re-fetch and
        // bounce back to Step 1 with the message from spec §848.
        await fetchPreview(true);
        return;
      }
      dispatch({ type: "submit/err", err: e });
    }
  }, [
    preview,
    me.is_owner,
    futureBookings,
    state.decisions,
    state.reason,
    state.notifyReassignedMasters,
    state.customTemplate,
    masterId,
    fetchPreview,
  ]);

  // --- render -----------------------------------------------------------

  if (!masterId) {
    return (
      <div className="screen">
        <StateError
          err={new ApiError(404, "bad_master_id", "ID мастера не задан")}
          onRetry={() => navigate("/admin/team")}
        />
      </div>
    );
  }

  if (state.loadingPreview && !preview) {
    return (
      <div className="screen">
        <h1 className="screen__title">Деактивация мастера</h1>
        <div className="callout" role="status">
          Считаю будущие записи…
        </div>
      </div>
    );
  }

  if (state.previewErr) {
    return (
      <div className="screen">
        <h1 className="screen__title">Деактивация мастера</h1>
        <StateError err={state.previewErr} onRetry={() => void fetchPreview()} />
      </div>
    );
  }

  if (!preview) return null;

  const masterName = preview.master.name;
  const masterFirst = firstName(masterName);
  const totalBookings = preview.summary.total_future_bookings;

  // Step 4 — result.
  if (state.step === 4 && state.result) {
    return (
      <div className="screen admin-flow-screen">
        <div style={{ textAlign: "center", padding: "var(--s-6) 0" }}>
          <div style={{ fontSize: "48px", lineHeight: 1 }} aria-hidden="true">
            ✓
          </div>
          <h1 className="screen__title" style={{ marginTop: "var(--s-3)" }}>
            {`${masterFirst} деактивирована`}
          </h1>
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: "var(--s-4) 0",
              textAlign: "start",
              maxWidth: "320px",
              marginInline: "auto",
            }}
          >
            {state.result.summary.reassigned_count > 0 && (
              <li>
                {`• ${state.result.summary.reassigned_count} записей переведено${reassignTargetNames.length ? ` на ${reassignTargetNames.join(", ")}` : ""}`}
              </li>
            )}
            {state.result.summary.cancelled_count > 0 && (
              <li>{`• ${state.result.summary.cancelled_count} записей отменены с уведомлением`}</li>
            )}
            <li>{`• ${masterFirst} больше не получает push`}</li>
          </ul>
          <p style={{ color: "var(--c-text-secondary)" }}>
            Все данные сохранены. Можно вернуть из архива.
          </p>
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
              className="cta-bar__button"
              onClick={() => navigate("/admin/team?is_active=false")}
            >
              Открыть архив команды
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => navigate("/admin/team")}
            >
              Вернуться к мастерам
            </button>
          </div>
        </div>
      </div>
    );
  }

  // --- Step 1 ---
  if (state.step === 1) {
    return (
      <div className="screen admin-flow-screen">
        <button
          type="button"
          className="admin-flow-back"
          onClick={() => navigate("/admin/team")}
        >
          ← Назад
        </button>
        <h1 className="screen__title">{`Деактивировать ${masterName}?`}</h1>
        <p>
          {`${masterFirst} станет неактивна — войти в систему не сможет, новых записей помощник к ней не предложит.`}
        </p>
        <p>
          Все данные сохранятся. Можно вернуть в любой момент одним кликом.
        </p>

        <div
          className="callout"
          style={{ marginTop: "var(--s-4)" }}
          aria-live="polite"
        >
          <div
            style={{
              fontWeight: 600,
              marginBottom: "var(--s-2)",
            }}
          >
            {`Будущие записи ${masterFirst}`}
          </div>
          {hasFutureBookings ? (
            <p style={{ margin: 0 }}>
              {`У ${masterFirst} ${totalBookings} будущих записей. Их нужно переназначить или отменить — иначе клиенты придут на закрытую дверь.`}
            </p>
          ) : (
            <p style={{ margin: 0 }}>
              {`У ${masterFirst} нет будущих записей. ✓ Можно деактивировать без переноса.`}
            </p>
          )}
        </div>

        <div
          style={{
            display: "flex",
            gap: "var(--s-2)",
            marginTop: "var(--s-5)",
          }}
        >
          <button
            type="button"
            className="btn-secondary"
            style={{ flex: 1 }}
            onClick={() => navigate("/admin/team")}
          >
            Отмена
          </button>
          <button
            type="button"
            className="cta-bar__button"
            style={{ flex: 1 }}
            onClick={handleStep1Continue}
          >
            {hasFutureBookings ? "Посмотреть и переназначить →" : "Продолжить →"}
          </button>
        </div>

        <Snackbar
          visible={!!state.toast}
          message={state.toast}
          onTimeout={() => dispatch({ type: "toast", value: "" })}
          onDismiss={() => dispatch({ type: "toast", value: "" })}
        />
      </div>
    );
  }

  // --- Step 2 ---
  if (state.step === 2) {
    return (
      <div className="screen admin-flow-screen">
        <button
          type="button"
          className="admin-flow-back"
          onClick={() => dispatch({ type: "step/set", step: 1 })}
        >
          ← Назад · Шаг 2 из 3
        </button>
        <h1 className="screen__title">
          {`Что делать с ${totalBookings} будущими записями ${masterFirst}?`}
        </h1>

        <div className="admin-bookings-list">
          {futureBookings.map((booking) => {
            const decision = state.decisions[booking.booking_id];
            const goodFallbacks = booking.fallback_masters.filter(
              (fm) => fm.match_score >= 100,
            );
            const partialFallbacks = booking.fallback_masters.filter(
              (fm) => fm.match_score > 0 && fm.match_score < 100,
            );
            const noFallback = booking.fallback_masters.length === 0;
            const orderedFallbacks = [...goodFallbacks, ...partialFallbacks];
            return (
              <div
                key={booking.booking_id}
                className="admin-booking-card"
              >
                <div className="admin-booking-card__head">
                  {`${bookingDateHuman(booking.visit_at)} · ${booking.service_name} · ${booking.client_first_name} ${booking.client_last_initial}`}
                </div>
                <fieldset className="admin-booking-card__choices">
                  <legend className="visually-hidden">{`Что делать с записью ${booking.client_first_name} ${booking.client_last_initial}`}</legend>
                  <label
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "var(--s-2)",
                      padding: "var(--s-2) 0",
                      opacity: noFallback ? 0.55 : 1,
                    }}
                  >
                    <input
                      type="radio"
                      name={`b-${booking.booking_id}`}
                      disabled={noFallback}
                      checked={decision?.action === "reassign"}
                      onChange={() => {
                        const first = orderedFallbacks[0];
                        if (!first) return;
                        dispatch({
                          type: "decision/set",
                          bookingId: booking.booking_id,
                          decision: {
                            action: "reassign",
                            to_master_id: first.master_id,
                          },
                        });
                      }}
                    />
                    <span style={{ flex: 1 }}>
                      <span style={{ display: "block", fontWeight: 500 }}>
                        Перенести на:
                      </span>
                      {noFallback ? (
                        <span className="admin-chip admin-chip--warn">
                          {`Никто из мастеров не выполняет «${booking.service_name}».`}
                        </span>
                      ) : (
                        <select
                          aria-label="Выбрать мастера для переноса"
                          disabled={decision?.action !== "reassign"}
                          value={decision?.to_master_id ?? ""}
                          onChange={(e) =>
                            dispatch({
                              type: "decision/set",
                              bookingId: booking.booking_id,
                              decision: {
                                action: "reassign",
                                to_master_id: e.target.value,
                              },
                            })
                          }
                          className="admin-select"
                        >
                          {orderedFallbacks.map((fm) => (
                            <option key={fm.master_id} value={fm.master_id}>
                              {fm.match_score >= 100 ? "✓ " : ""}
                              {fm.name}
                              {fm.match_score < 100 ? " (частично)" : ""}
                            </option>
                          ))}
                        </select>
                      )}
                    </span>
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
                      name={`b-${booking.booking_id}`}
                      checked={decision?.action === "cancel"}
                      onChange={() =>
                        dispatch({
                          type: "decision/set",
                          bookingId: booking.booking_id,
                          decision: { action: "cancel" },
                        })
                      }
                    />
                    <span style={{ flex: 1 }}>
                      <span style={{ display: "block", fontWeight: 500 }}>
                        Отменить запись
                      </span>
                      <span style={{ color: "var(--c-text-secondary)" }}>
                        Клиент получит извинение и предложение нового слота.
                      </span>
                    </span>
                  </label>
                </fieldset>
              </div>
            );
          })}
        </div>

        <div className="admin-flow-summary" aria-live="polite">
          {`Сводка: ${reassignCount} переносим · ${cancelCount} отменяем · ${undecidedCount} не решено`}
        </div>

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
            onClick={() => dispatch({ type: "step/set", step: 1 })}
          >
            Назад
          </button>
          <button
            type="button"
            className="cta-bar__button"
            style={{ flex: 1 }}
            disabled={undecidedCount > 0}
            onClick={handleStep2Continue}
          >
            Продолжить →
          </button>
        </div>

        <Snackbar
          visible={!!state.toast}
          message={state.toast}
          onTimeout={() => dispatch({ type: "toast", value: "" })}
          onDismiss={() => dispatch({ type: "toast", value: "" })}
        />
      </div>
    );
  }

  // --- Step 3 ---
  const tenantName = me.tenant.name;
  const defaultTemplate = `От: Помощник Студии ${tenantName}\n\nЗдравствуйте, {{client_first_name}}! По вашей записи на {{visit_at}} — {{old_master_first_name}}, к сожалению, больше не работает в студии.\nВашу запись переведём к {{new_master_first_name}} — она/он тоже делает {{service_name}}, отзывы отличные.\nЕсли так не подходит — напишите, я предложу другие варианты. 🙏`;
  const previewTemplate = state.customTemplate || defaultTemplate;

  const stepNumber = hasFutureBookings ? "Шаг 3 из 3" : "Шаг 2 из 2";

  return (
    <div className="screen admin-flow-screen">
      <button
        type="button"
        className="admin-flow-back"
        onClick={() =>
          dispatch({
            type: "step/set",
            step: (hasFutureBookings ? 2 : 1) as Step,
          })
        }
      >
        {`← Назад · ${stepNumber}`}
      </button>
      <h1 className="screen__title">Подтвердите изменения</h1>

      <div className="callout">
        <p style={{ margin: 0 }}>{`${masterName} будет деактивирована.`}</p>
        {hasFutureBookings && (
          <>
            <p style={{ margin: "var(--s-2) 0 0" }}>
              {`Из ${totalBookings} будущих записей:`}
            </p>
            <ul style={{ margin: "var(--s-1) 0 0", paddingInlineStart: "var(--s-4)" }}>
              {reassignCount > 0 && (
                <li>
                  {`${reassignCount} перенесём на ${reassignTargetNames.length ? reassignTargetNames.join(", ") : "выбранных мастеров"}`}
                </li>
              )}
              {cancelCount > 0 && <li>{`${cancelCount} отменим`}</li>}
            </ul>
          </>
        )}
      </div>

      {hasFutureBookings && reassignCount > 0 && (
        <>
          <h2
            style={{
              fontSize: "var(--font-size-300)",
              marginTop: "var(--s-4)",
              marginBottom: "var(--s-2)",
            }}
          >
            Что увидят клиенты (предпросмотр сообщения):
          </h2>
          <pre
            className="admin-template-preview"
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              font: "inherit",
              background: "var(--c-surface-2)",
              padding: "var(--s-3)",
              borderRadius: "var(--r-md)",
              margin: 0,
            }}
          >
            {previewTemplate}
          </pre>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={() => setShowTemplateEdit(true)}
          >
            Изменить текст
          </button>
        </>
      )}

      {hasFutureBookings && cancelCount > 0 && (
        <p style={{ marginTop: "var(--s-3)", color: "var(--c-text-secondary)" }}>
          Запись на это время отменим. Если хотите, могу предложить другие свободные слоты — напишите.
        </p>
      )}

      <label
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "var(--s-2)",
          marginTop: "var(--s-4)",
        }}
      >
        <input
          type="checkbox"
          checked={state.notifyReassignedMasters}
          onChange={(e) =>
            dispatch({ type: "notify/toggle", value: e.target.checked })
          }
        />
        <span>Уведомить переназначенных мастеров</span>
      </label>

      <label
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "var(--s-2)",
          marginTop: "var(--s-2)",
          color: "var(--c-text-secondary)",
        }}
      >
        <input type="checkbox" checked readOnly />
        <span>Записать причину в журнал</span>
      </label>

      <div style={{ marginTop: "var(--s-3)" }}>
        <label
          htmlFor="reason-textarea"
          style={{ display: "block", marginBottom: "var(--s-1)" }}
        >
          Причина (необязательно)
        </label>
        <textarea
          id="reason-textarea"
          rows={3}
          value={state.reason}
          maxLength={1000}
          onChange={(e) =>
            dispatch({ type: "reason/set", value: e.target.value })
          }
          className="admin-textarea"
        />
      </div>

      {state.submitErr instanceof ApiError && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginTop: "var(--s-3)" }}
        >
          {state.submitErr.detail || "Не получилось деактивировать"}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: "var(--s-2)",
          marginTop: "var(--s-5)",
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          style={{ flex: 1 }}
          disabled={state.submitting}
          onClick={() =>
            dispatch({
              type: "step/set",
              step: (hasFutureBookings ? 2 : 1) as Step,
            })
          }
        >
          Назад
        </button>
        <button
          type="button"
          className="btn-danger"
          style={{ flex: 1 }}
          disabled={state.submitting || !me.is_owner}
          title={me.is_owner ? undefined : ownerOnlyDisabledLabel}
          onClick={() => void handleSubmit()}
        >
          {state.submitting
            ? "Деактивируем…"
            : `Деактивировать ${masterFirst}`}
        </button>
      </div>

      {/* Template edit modal */}
      {showTemplateEdit && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Изменить текст уведомления"
          onClick={() => setShowTemplateEdit(false)}
        >
          <div className="admin-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              Текст уведомления клиенту
            </div>
            <textarea
              rows={10}
              value={state.customTemplate || defaultTemplate}
              maxLength={4000}
              onChange={(e) =>
                dispatch({ type: "template/set", value: e.target.value })
              }
              className="admin-textarea"
              style={{ width: "100%" }}
            />
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
              <button
                type="button"
                className="btn-secondary"
                style={{ flex: 1 }}
                onClick={() => {
                  dispatch({ type: "template/set", value: "" });
                  setShowTemplateEdit(false);
                }}
              >
                Сбросить
              </button>
              <button
                type="button"
                className="cta-bar__button"
                style={{ flex: 1 }}
                onClick={() => setShowTemplateEdit(false)}
              >
                Готово
              </button>
            </div>
          </div>
        </div>
      )}

      <Snackbar
        visible={!!state.toast}
        message={state.toast}
        onTimeout={() => dispatch({ type: "toast", value: "" })}
        onDismiss={() => dispatch({ type: "toast", value: "" })}
      />
    </div>
  );
}
