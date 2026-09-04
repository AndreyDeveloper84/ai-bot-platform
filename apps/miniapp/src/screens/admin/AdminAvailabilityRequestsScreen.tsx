/**
 * Admin availability requests — M3-admin approve/reject UI (Bundle B).
 *
 * Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M3
 *       (lines ~458-476) — «master taps empty slot → mark unavailable
 *       → owner approves async» — this screen is the owner-side complement.
 *
 *       docs/design/handoffs/2026-05-18-master-management-handoff.md —
 *       admin chrome / role gating.
 *
 * Backend: PR #521 (merged c49d142).
 *   GET    /api/v1/admin/availability-requests/?status=pending|decided|all
 *   POST   /api/v1/admin/availability-requests/<uuid>/approve/
 *   POST   /api/v1/admin/availability-requests/<uuid>/reject/   body {rejection_reason}
 *
 * Role gating: Owner + Admin only. Receptionist sees a «только для
 * администраторов» banner — backend re-checks at every endpoint
 * (defence in depth).
 *
 * 409 handling — bypasses the shared ``request`` helper so the
 *   ``{error, detail}`` envelope isn't flattened into ApiError.detail.
 *   See ``decisionFetch`` in ``../lib/admin-api.ts`` for the same
 *   rationale used by ``patchServicesMapping`` (PR #518).
 *
 * Reason-class derivation note: the backend ScheduleChangeRequest.ReasonClass
 *   enum ships ``vacation / sick / personal / other`` (4 values) — the
 *   spec brief listed ``vacation / sick_leave / day_off / study / personal``
 *   (5 mixed slugs from a different enum). We render the actual backend
 *   slugs with the model's Russian labels («Отпуск», «Больничный»,
 *   «Личные дела», «Другое») and gracefully fall back to the raw slug
 *   for any future-added value.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import { Snackbar } from "../../components/Snackbar";
import { StateError } from "../../components/StateError";
import { ApiError } from "../../lib/api";
import {
  approveAvailabilityRequest,
  getAvailabilityRequests,
  rejectAvailabilityRequest,
  type AvailabilityRequestItem,
  type MeResponse,
} from "../../lib/admin-api";
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

type FilterTab = "pending" | "decided" | "all";

const REASON_LABEL: Record<string, string> = {
  vacation: "Отпуск",
  sick: "Больничный",
  // sick_leave is the ScheduleException.Type slug; some downstream
  // emitters might use it — accept both for safety.
  sick_leave: "Больничный",
  personal: "Личные дела",
  day_off: "Выходной",
  study: "Учёба",
  other: "Другое",
};

const REASON_TEXT_TRUNCATE = 80;

const MONTHS_GEN = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

function pluralDays(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} день`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    return `${n} дня`;
  }
  return `${n} дней`;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * Render «12–15 июня (4 дня)» for multi-day windows or
 * «25 мая, 14:00–18:00» for single-day sub-24h windows. Falls back
 * to a generic «—» when either endpoint is missing.
 */
function formatRange(startIso: string | null, endIso: string | null): string {
  if (!startIso || !endIso) return "—";
  const start = new Date(startIso);
  const end = new Date(endIso);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "—";

  // Walk dates in local TZ. The exclusive-end convention from the
  // backend (`_covered_dates`) maps a `[00:00, 00:00 next-day)`
  // window to one date; subtract 1ms to mirror that behaviour.
  const endInclusive = new Date(end.getTime() - 1);
  const startY = start.getFullYear();
  const startM = start.getMonth();
  const startD = start.getDate();
  const endY = endInclusive.getFullYear();
  const endM = endInclusive.getMonth();
  const endD = endInclusive.getDate();

  const sameDay = startY === endY && startM === endM && startD === endD;

  if (sameDay) {
    const dayLabel = `${startD} ${MONTHS_GEN[startM]}`;
    const timeRange = `${pad2(start.getHours())}:${pad2(start.getMinutes())}–${pad2(end.getHours())}:${pad2(end.getMinutes())}`;
    return `${dayLabel}, ${timeRange}`;
  }

  // Multi-day range — count covered local dates inclusive.
  const dayMs = 24 * 60 * 60 * 1000;
  const startMidnight = new Date(startY, startM, startD).getTime();
  const endMidnight = new Date(endY, endM, endD).getTime();
  const numDays = Math.round((endMidnight - startMidnight) / dayMs) + 1;

  const sameMonth = startM === endM && startY === endY;
  const label = sameMonth
    ? `${startD}–${endD} ${MONTHS_GEN[startM]}`
    : `${startD} ${MONTHS_GEN[startM]} — ${endD} ${MONTHS_GEN[endM]}`;
  return `${label} (${pluralDays(numDays)})`;
}

function formatDecidedAt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function reasonLabel(slug: string): string {
  return REASON_LABEL[slug] || slug || "—";
}

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("");
}

interface RejectModalState {
  open: boolean;
  request: AvailabilityRequestItem | null;
  reason: string;
  submitting: boolean;
  errorMsg: string;
}

const EMPTY_REJECT_STATE: RejectModalState = {
  open: false,
  request: null,
  reason: "",
  submitting: false,
  errorMsg: "",
};

interface ConfirmApproveState {
  open: boolean;
  request: AvailabilityRequestItem | null;
  submitting: boolean;
}

const EMPTY_APPROVE_STATE: ConfirmApproveState = {
  open: false,
  request: null,
  submitting: false,
};

interface OverlapBannerState {
  dates: string[];
  detail: string;
}

const MAX_REJECTION_REASON_LEN = 500;

export function AdminAvailabilityRequestsScreen({ me }: Props) {
  const navigate = useNavigate();

  const isAdmin = me.is_owner || me.is_admin;

  const [filter, setFilter] = useState<FilterTab>("pending");
  const [items, setItems] = useState<AvailabilityRequestItem[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [err, setErr] = useState<unknown>(null);
  const [pendingCount, setPendingCount] = useState<number>(0);
  const [decidedCount, setDecidedCount] = useState<number>(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const [approveState, setApproveState] = useState<ConfirmApproveState>(
    EMPTY_APPROVE_STATE,
  );
  const [rejectState, setRejectState] = useState<RejectModalState>(
    EMPTY_REJECT_STATE,
  );
  const [overlapBanner, setOverlapBanner] =
    useState<OverlapBannerState | null>(null);
  const [alreadyDecidedBanner, setAlreadyDecidedBanner] = useState<boolean>(
    false,
  );
  const [toast, setToast] = useState<string>("");

  // --- BackButton wiring -------------------------------------------------
  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => navigate("/admin/team"));
    return () => {
      off();
      setBackButton(false);
    };
  }, [navigate]);

  // --- closingConfirmation while reject modal has dirty input -----------
  useEffect(() => {
    const dirty =
      rejectState.open && rejectState.reason.trim().length > 0;
    setClosingConfirmation(dirty);
    return () => setClosingConfirmation(false);
  }, [rejectState.open, rejectState.reason]);

  // --- Load list (status-filtered) --------------------------------------
  const load = useCallback(
    async (
      statusFilter: FilterTab,
      cursor?: string,
      signal?: AbortSignal,
    ) => {
      const isFirstPage = !cursor;
      if (isFirstPage) {
        setLoading(true);
        setErr(null);
      } else {
        setLoadingMore(true);
      }
      try {
        const res = await getAvailabilityRequests(
          {
            status: statusFilter,
            cursor,
            limit: 25,
          },
          { signal },
        );
        if (signal?.aborted) return;
        setItems((prev) =>
          isFirstPage ? res.items : [...(prev || []), ...res.items],
        );
        setNextCursor(res.next_cursor);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        if (isFirstPage) {
          setErr(e);
          setItems(null);
        } else {
          const msg =
            e instanceof ApiError
              ? e.detail || "Не получилось загрузить ещё"
              : "Сеть недоступна — попробуйте ещё раз";
          setToast(msg);
        }
      } finally {
        if (signal?.aborted) return;
        if (isFirstPage) setLoading(false);
        else setLoadingMore(false);
      }
    },
    [],
  );

  // Refresh small pending count + decided count for the filter chips
  // (limit=1 keeps the round-trip cheap; the backend doesn't expose a
  // dedicated count endpoint).
  const reloadCounts = useCallback(async (signal?: AbortSignal) => {
    try {
      const [pendingRes, decidedRes] = await Promise.all([
        getAvailabilityRequests({ status: "pending", limit: 50 }, { signal }),
        getAvailabilityRequests({ status: "decided", limit: 50 }, { signal }),
      ]);
      if (signal?.aborted) return;
      setPendingCount(pendingRes.items.length);
      setDecidedCount(decidedRes.items.length);
    } catch {
      // Counts are best-effort — silent fail keeps the chips visible
      // with stale or zero numbers; the main list path surfaces errors.
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    const controller = new AbortController();
    void load(filter, undefined, controller.signal);
    return () => controller.abort();
  }, [filter, isAdmin, load]);

  useEffect(() => {
    if (!isAdmin) return;
    const controller = new AbortController();
    void reloadCounts(controller.signal);
    return () => controller.abort();
  }, [isAdmin, reloadCounts]);

  const handleFilterChange = useCallback(
    (next: FilterTab) => {
      hapticSelection();
      setFilter(next);
      setOverlapBanner(null);
      setAlreadyDecidedBanner(false);
    },
    [],
  );

  const refresh = useCallback(() => {
    void load(filter);
    void reloadCounts();
  }, [filter, load, reloadCounts]);

  const toggleExpanded = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // --- Approve flow ------------------------------------------------------
  const openApprove = useCallback((item: AvailabilityRequestItem) => {
    hapticImpact("light");
    setApproveState({ open: true, request: item, submitting: false });
  }, []);

  const closeApprove = useCallback(() => {
    setApproveState(EMPTY_APPROVE_STATE);
  }, []);

  const confirmApprove = useCallback(async () => {
    const req = approveState.request;
    if (!req) return;
    setApproveState((s) => ({ ...s, submitting: true }));
    try {
      const result = await approveAvailabilityRequest(req.request_id);
      if ("__conflict" in result) {
        // 409 — branch on slug.
        if (result.conflict === "overlap_conflict") {
          setOverlapBanner({
            dates: result.dates || [],
            detail: result.detail,
          });
          hapticNotify("error");
          setApproveState(EMPTY_APPROVE_STATE);
          return;
        }
        // already_decided — silent reload + soft banner.
        setAlreadyDecidedBanner(true);
        setApproveState(EMPTY_APPROVE_STATE);
        hapticNotify("warning");
        refresh();
        return;
      }
      // Optimistic update + toast.
      hapticNotify("success");
      setItems((prev) => {
        if (!prev) return prev;
        if (filter === "pending") {
          return prev.filter((p) => p.request_id !== req.request_id);
        }
        return prev.map((p) =>
          p.request_id === req.request_id ? { ...p, ...result } : p,
        );
      });
      setPendingCount((c) => Math.max(0, c - 1));
      setDecidedCount((c) => c + 1);
      setToast(`✓ Запрос ${req.master_name} одобрен`);
      setApproveState(EMPTY_APPROVE_STATE);
    } catch (e) {
      hapticNotify("error");
      const msg =
        e instanceof ApiError
          ? e.detail || "Не получилось одобрить"
          : "Сеть недоступна — попробуйте ещё раз";
      setToast(msg);
      setApproveState((s) => ({ ...s, submitting: false }));
    }
  }, [approveState.request, filter, refresh]);

  // --- Reject flow -------------------------------------------------------
  const openReject = useCallback((item: AvailabilityRequestItem) => {
    hapticImpact("light");
    setRejectState({
      open: true,
      request: item,
      reason: "",
      submitting: false,
      errorMsg: "",
    });
  }, []);

  const closeReject = useCallback(() => {
    setRejectState(EMPTY_REJECT_STATE);
  }, []);

  const submitReject = useCallback(async () => {
    const req = rejectState.request;
    if (!req) return;
    const trimmed = rejectState.reason.trim();
    if (!trimmed) {
      setRejectState((s) => ({
        ...s,
        errorMsg: "Укажите причину отказа",
      }));
      return;
    }
    if (trimmed.length > MAX_REJECTION_REASON_LEN) {
      setRejectState((s) => ({
        ...s,
        errorMsg: `Не больше ${MAX_REJECTION_REASON_LEN} символов`,
      }));
      return;
    }
    setRejectState((s) => ({ ...s, submitting: true, errorMsg: "" }));
    try {
      const result = await rejectAvailabilityRequest(req.request_id, trimmed);
      if ("__conflict" in result) {
        if (result.conflict === "already_decided") {
          setAlreadyDecidedBanner(true);
          setRejectState(EMPTY_REJECT_STATE);
          hapticNotify("warning");
          refresh();
          return;
        }
        // Overlap conflict on reject is not expected by the backend
        // (reject doesn't materialise) — surface generically just in case.
        setRejectState((s) => ({
          ...s,
          submitting: false,
          errorMsg: result.detail || "Конфликт. Перезагрузите список.",
        }));
        hapticNotify("error");
        return;
      }
      hapticNotify("success");
      setItems((prev) => {
        if (!prev) return prev;
        if (filter === "pending") {
          return prev.filter((p) => p.request_id !== req.request_id);
        }
        return prev.map((p) =>
          p.request_id === req.request_id ? { ...p, ...result } : p,
        );
      });
      setPendingCount((c) => Math.max(0, c - 1));
      setDecidedCount((c) => c + 1);
      setToast(`✗ Запрос ${req.master_name} отклонён`);
      setRejectState(EMPTY_REJECT_STATE);
    } catch (e) {
      hapticNotify("error");
      const msg =
        e instanceof ApiError
          ? e.detail || "Не получилось отклонить"
          : "Сеть недоступна — попробуйте ещё раз";
      // Preserve user's typed reason on failure.
      setRejectState((s) => ({
        ...s,
        submitting: false,
        errorMsg: msg,
      }));
    }
  }, [rejectState.request, rejectState.reason, filter, refresh]);

  const loadMore = useCallback(() => {
    if (!nextCursor || loadingMore) return;
    void load(filter, nextCursor);
  }, [filter, load, loadingMore, nextCursor]);

  // --- Render guards -----------------------------------------------------
  if (!isAdmin) {
    return (
      <div className="screen">
        <h1 className="screen__title">Запросы на смену графика</h1>
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>
            Эта страница только для администраторов.
          </p>
        </div>
        <AdminTabBar />
      </div>
    );
  }

  const reasonCount = useMemo(() => {
    if (!items) return { pending: 0, decided: 0 };
    let p = 0;
    let d = 0;
    for (const it of items) {
      if (it.status === "pending") p += 1;
      else d += 1;
    }
    return { pending: p, decided: d };
  }, [items]);

  void reasonCount; // memo retained for future inline counts

  return (
    <div className="screen">
      <header
        className="screen__header"
        style={{
          marginBottom: "var(--s-3)",
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)",
        }}
      >
        <h1
          className="screen__title"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--s-2)",
            margin: 0,
          }}
        >
          Запросы на смену графика
          {pendingCount > 0 && (
            <span
              className="admin-count-chip"
              aria-label={`ожидают: ${pendingCount}`}
            >
              {pendingCount}
            </span>
          )}
        </h1>
      </header>

      <div
        role="tablist"
        aria-label="Фильтр запросов"
        className="admin-filter-tabs"
      >
        <button
          type="button"
          role="tab"
          aria-selected={filter === "pending"}
          className={`admin-filter-tabs__tab${filter === "pending" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => handleFilterChange("pending")}
        >
          {`● Ожидают (${pendingCount})`}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filter === "decided"}
          className={`admin-filter-tabs__tab${filter === "decided" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => handleFilterChange("decided")}
        >
          {`Решены (${decidedCount})`}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filter === "all"}
          className={`admin-filter-tabs__tab${filter === "all" ? " admin-filter-tabs__tab--active" : ""}`}
          onClick={() => handleFilterChange("all")}
        >
          Все
        </button>
      </div>

      {alreadyDecidedBanner && (
        <div
          className="callout"
          role="status"
          style={{ marginTop: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>Этот запрос уже был решён.</p>
          <div style={{ marginTop: "var(--s-2)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setAlreadyDecidedBanner(false)}
            >
              Скрыть
            </button>
          </div>
        </div>
      )}

      {overlapBanner && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ marginTop: "var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>
            Не удалось одобрить — на этих датах уже есть другие изменения:
            {" "}
            {overlapBanner.dates.length > 0
              ? overlapBanner.dates.join(", ")
              : overlapBanner.detail}
            .
          </p>
          <div style={{ marginTop: "var(--s-2)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setOverlapBanner(null);
                refresh();
              }}
            >
              Перезагрузить
            </button>
          </div>
        </div>
      )}

      {err ? (
        <div style={{ marginTop: "var(--s-3)" }}>
          <StateError err={err} onRetry={refresh} />
        </div>
      ) : null}

      {loading && items === null && !err && (
        <ul
          className="admin-master-list"
          style={{ listStyle: "none", padding: 0, margin: "var(--s-3) 0 0" }}
          aria-hidden="true"
        >
          {[0, 1, 2].map((i) => (
            <li key={i}>
              <div className="master-card">
                <span
                  className="skeleton"
                  style={{ width: 48, height: 48, borderRadius: "50%" }}
                />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span
                    className="skeleton"
                    style={{
                      display: "block",
                      height: 14,
                      width: "60%",
                      marginBottom: 8,
                    }}
                  />
                  <span
                    className="skeleton"
                    style={{ display: "block", height: 12, width: "40%" }}
                  />
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {!loading && !err && items && items.length === 0 && (
        <div
          className="callout"
          role="status"
          style={{ marginTop: "var(--s-3)" }}
        >
          {filter === "pending"
            ? "Все запросы рассмотрены 👍"
            : filter === "decided"
              ? "Пока никто не запрашивал смены графика"
              : "Запросов нет"}
        </div>
      )}

      {items && items.length > 0 && (
        <ul
          className="admin-master-list"
          style={{ listStyle: "none", padding: 0, margin: "var(--s-3) 0 0" }}
        >
          {items.map((it) => {
            const isPending = it.status === "pending";
            const isExpanded = expanded.has(it.request_id);
            const reasonText = it.reason_text || "";
            const tooLong = reasonText.length > REASON_TEXT_TRUNCATE;
            const reasonDisplay =
              tooLong && !isExpanded
                ? `${reasonText.slice(0, REASON_TEXT_TRUNCATE)}…`
                : reasonText;
            return (
              <li key={it.request_id}>
                <div className="master-card" style={{ alignItems: "flex-start" }}>
                  <span
                    className="master-card__avatar"
                    style={{ background: "var(--c-surface-2)" }}
                  >
                    <span aria-hidden="true">{initials(it.master_name)}</span>
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "var(--s-2)",
                        flexWrap: "wrap",
                      }}
                    >
                      <span className="master-card__name">
                        {it.master_name || "—"}
                      </span>
                      <span className="admin-chip">
                        {reasonLabel(it.reason_class)}
                      </span>
                    </span>
                    <span
                      className="master-card__spec"
                      style={{ display: "block", marginTop: "var(--s-1)" }}
                    >
                      {formatRange(it.requested_start, it.requested_end)}
                    </span>
                    {reasonText && (
                      <span
                        style={{
                          display: "block",
                          marginTop: "var(--s-2)",
                          color: "var(--c-text-secondary)",
                          fontSize: "0.92rem",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                        }}
                      >
                        {reasonDisplay}
                        {tooLong && (
                          <button
                            type="button"
                            className="btn-link"
                            style={{
                              marginLeft: "var(--s-1)",
                              background: "none",
                              border: "none",
                              padding: 0,
                              color: "var(--c-accent)",
                              cursor: "pointer",
                            }}
                            onClick={() => toggleExpanded(it.request_id)}
                          >
                            {isExpanded ? "свернуть" : "развернуть"}
                          </button>
                        )}
                      </span>
                    )}
                    {isPending ? (
                      <span
                        style={{
                          display: "flex",
                          gap: "var(--s-2)",
                          marginTop: "var(--s-3)",
                          flexWrap: "wrap",
                        }}
                      >
                        <button
                          type="button"
                          className="cta-bar__button"
                          style={{ padding: "var(--s-2) var(--s-3)" }}
                          onClick={() => openApprove(it)}
                        >
                          ✓ Одобрить
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          style={{ padding: "var(--s-2) var(--s-3)" }}
                          onClick={() => openReject(it)}
                        >
                          ✗ Отклонить
                        </button>
                      </span>
                    ) : (
                      <span
                        style={{
                          display: "block",
                          marginTop: "var(--s-2)",
                          color: "var(--c-text-secondary)",
                          fontSize: "0.92rem",
                        }}
                      >
                        {it.status === "approved" ? (
                          <>
                            {`✓ Одобрено${it.decided_by_name ? ` • ${it.decided_by_name}` : ""}${it.decided_at ? ` • ${formatDecidedAt(it.decided_at)}` : ""}`}
                          </>
                        ) : it.status === "rejected" ? (
                          <>
                            {`✗ Отклонено${it.decided_by_name ? ` • ${it.decided_by_name}` : ""}${it.decided_at ? ` • ${formatDecidedAt(it.decided_at)}` : ""}`}
                            {it.resolution_note ? ` — ${it.resolution_note}` : ""}
                          </>
                        ) : (
                          String(it.status)
                        )}
                      </span>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {nextCursor && items && items.length > 0 && (
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            margin: "var(--s-4) 0",
          }}
        >
          <button
            type="button"
            className="btn-secondary"
            disabled={loadingMore}
            onClick={loadMore}
          >
            {loadingMore ? "Загружаем…" : "Показать ещё ▾"}
          </button>
        </div>
      )}

      {/* --- Approve confirm sheet --- */}
      {approveState.open && approveState.request && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Подтвердить одобрение"
          onClick={() => !approveState.submitting && closeApprove()}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              Одобрить запрос
            </div>
            <p style={{ margin: "var(--s-2) 0" }}>
              {`Одобрить запрос ${approveState.request.master_name} на ${formatRange(approveState.request.requested_start, approveState.request.requested_end)}? Это пометит дни как нерабочие.`}
            </p>
            <div
              style={{
                display: "flex",
                gap: "var(--s-2)",
                marginTop: "var(--s-3)",
              }}
            >
              <button
                type="button"
                className="btn-secondary"
                disabled={approveState.submitting}
                onClick={closeApprove}
                style={{ flex: 1 }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={approveState.submitting}
                onClick={() => void confirmApprove()}
                style={{ flex: 1 }}
              >
                {approveState.submitting ? "Одобряем…" : "Одобрить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- Reject modal --- */}
      {rejectState.open && rejectState.request && (
        <div
          className="admin-sheet-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Отклонить запрос"
          onClick={() => !rejectState.submitting && closeReject()}
        >
          <div
            className="admin-sheet"
            onClick={(e) => e.stopPropagation()}
            style={{ textAlign: "start" }}
          >
            <div className="admin-sheet__title" style={{ textAlign: "start" }}>
              {`Отклонить запрос ${rejectState.request.master_name}`}
            </div>
            <p style={{ margin: "var(--s-2) 0", color: "var(--c-text-secondary)" }}>
              {`${formatRange(rejectState.request.requested_start, rejectState.request.requested_end)} — ${reasonLabel(rejectState.request.reason_class)}`}
            </p>
            <label
              htmlFor="reject-reason"
              style={{ display: "block", marginTop: "var(--s-3)" }}
            >
              Почему отклоняете?
            </label>
            <textarea
              id="reject-reason"
              className="admin-search-input"
              style={{
                width: "100%",
                minHeight: 96,
                marginTop: "var(--s-2)",
                resize: "vertical",
              }}
              value={rejectState.reason}
              maxLength={MAX_REJECTION_REASON_LEN + 50}
              onChange={(e) =>
                setRejectState((s) => ({
                  ...s,
                  reason: e.target.value,
                  errorMsg: "",
                }))
              }
              placeholder="Например: на эти даты много записей"
              disabled={rejectState.submitting}
            />
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginTop: "var(--s-1)",
                fontSize: "0.85rem",
                color:
                  rejectState.reason.trim().length > MAX_REJECTION_REASON_LEN
                    ? "var(--c-danger)"
                    : "var(--c-text-secondary)",
              }}
            >
              <span>{rejectState.errorMsg || ""}</span>
              <span>
                {`${rejectState.reason.trim().length} / ${MAX_REJECTION_REASON_LEN}`}
              </span>
            </div>
            <div
              style={{
                display: "flex",
                gap: "var(--s-2)",
                marginTop: "var(--s-3)",
              }}
            >
              <button
                type="button"
                className="btn-secondary"
                disabled={rejectState.submitting}
                onClick={closeReject}
                style={{ flex: 1 }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="cta-bar__button"
                disabled={rejectState.submitting}
                onClick={() => void submitReject()}
                style={{ flex: 1 }}
              >
                {rejectState.submitting ? "Отклоняем…" : "Отклонить"}
              </button>
            </div>
          </div>
        </div>
      )}

      <Snackbar
        visible={!!toast}
        message={toast}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />

      <AdminTabBar />
    </div>
  );
}
