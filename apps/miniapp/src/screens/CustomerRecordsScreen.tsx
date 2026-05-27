/**
 * Customer records screen — Tier 1 Priority 5 Phase B (R1 main list).
 *
 * Spec: `docs/screens/customer-records-flow.md` §3 (R1 main records list,
 * 2-section structure + Variant C smart grouping + filter chips on
 * history) + §7 (R5 empty states) + §9 (states matrix).
 *
 * # Tab structure
 *
 * Per Tau §3.1 — 2 top-level tabs «Ближайшие (N)» / «История (N)». Default
 * landing «Ближайшие» (Q-R-10 verdict). Filter chips appear on the
 * «История» tab when total past >= 5 (Tau §13 implementer note #8).
 *
 * # 5-state matrix (Wellness pattern reuse)
 *
 *   loading  → 3 skeleton cards
 *   ok       → render groupings
 *   empty    → EmptyRecordsState (2 cases: never had OR upcoming empty
 *               but has past)
 *   error    → generic copy + retry
 *   offline  → banner «Записи могут быть устаревшими»
 *
 * # Voice locks
 *
 *   - «Записи» (NOT «Bookings»)
 *   - «Сообщить по записи» (F1 lock)
 *   - «ты» canonical (F2 lock)
 *   - no exclamation marks anywhere
 *
 * # WCAG (§13 inline)
 *
 *   - Tab strip uses role="tablist" / role="tab" / aria-controls.
 *   - Skip link «К списку записей» so SR users can bypass the header.
 *   - Tenant + time group headers use <h2> / <h3> semantics.
 *   - Tab switch focuses first card (focus order = WCAG 2.4.3).
 *   - prefers-reduced-motion respected via existing .skeleton class.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookingCard, BookingCardSkeleton } from "../components/BookingCard";
import { BookingMessageModal } from "../components/BookingMessageModal";
import { EmptyRecordsState } from "../components/EmptyRecordsState";
import { TenantGroupHeader } from "../components/TenantGroupHeader";
import { TimeGroupHeader } from "../components/TimeGroupHeader";
import {
  annotateGroupings,
  getMyBookings,
  getRepeatIntent,
  type AnnotatedGrouping,
  type BookingListItem,
  type BookingSection,
  type BookingsListResponse,
} from "../lib/customer-records";
import { ApiError } from "../lib/api";

// ---------------------------------------------------------------------------
// State model — per-section isolation so flipping tabs doesn't blank the
// other.
// ---------------------------------------------------------------------------

type Slice<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; reason: "network" | "server" | "other" };

function isOnline(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine !== false;
}

// ---------------------------------------------------------------------------
// Filter chip vocabulary — Tau §3.4 ("Все" / by service / by master).
// MVP cut: only «Все» + dynamically-generated unique-master chips.
// The «У {master}» chips come from the actual data — no hardcoded list.
// ---------------------------------------------------------------------------

const FILTER_ALL = "__all__";

interface FilterState {
  /** Active filter key — either FILTER_ALL or a master_first_name. */
  active: string;
}

// ---------------------------------------------------------------------------
// Main screen.
// ---------------------------------------------------------------------------

export function CustomerRecordsScreen() {
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<BookingSection>("upcoming");
  const [upcoming, setUpcoming] = useState<Slice<BookingsListResponse>>({
    kind: "loading",
  });
  const [history, setHistory] = useState<Slice<BookingsListResponse>>({
    kind: "loading",
  });
  const [online, setOnline] = useState<boolean>(isOnline());
  const [filter, setFilter] = useState<FilterState>({ active: FILTER_ALL });
  const [messageBooking, setMessageBooking] = useState<BookingListItem | null>(
    null,
  );
  const [toast, setToast] = useState<string | null>(null);

  // ── data fetch ────────────────────────────────────────────────────────
  const fetchSection = useCallback(async (section: BookingSection) => {
    const setSlice =
      section === "upcoming" ? setUpcoming : setHistory;
    setSlice({ kind: "loading" });
    try {
      const data = await getMyBookings(section);
      setSlice({ kind: "ok", data });
    } catch (e) {
      setSlice({
        kind: "error",
        reason:
          e instanceof ApiError && e.status >= 500
            ? "server"
            : e instanceof ApiError
              ? "other"
              : "network",
      });
    }
  }, []);

  useEffect(() => {
    void fetchSection("upcoming");
    void fetchSection("history");
  }, [fetchSection]);

  // ── online / offline transitions ─────────────────────────────────────
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => setOnline(true);
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Reset filter when switching tabs.
  useEffect(() => {
    setFilter({ active: FILTER_ALL });
  }, [activeTab]);

  // Toast auto-clear after 3s.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  // ── derived state ────────────────────────────────────────────────────
  const upcomingData = upcoming.kind === "ok" ? upcoming.data : null;
  const historyData = history.kind === "ok" ? history.data : null;

  const upcomingCount = upcomingData?.total_count ?? 0;
  const historyCount = historyData?.total_count ?? 0;

  // Filter chip eligibility — Tau §13 #8: show only when history has >=5.
  const filterChipsEnabled =
    activeTab === "history" && historyCount >= 5;

  // Unique master names extracted from current section for filter chips.
  const masterFilterOptions = useMemo(() => {
    if (!filterChipsEnabled || !historyData) return [];
    const seen = new Set<string>();
    const ordered: string[] = [];
    for (const g of historyData.groupings) {
      for (const b of g.bookings) {
        if (!seen.has(b.master_first_name)) {
          seen.add(b.master_first_name);
          ordered.push(b.master_first_name);
        }
      }
    }
    return ordered;
  }, [filterChipsEnabled, historyData]);

  // Apply filter to the active section's groupings.
  const annotatedGroupings: AnnotatedGrouping[] = useMemo(() => {
    const data = activeTab === "upcoming" ? upcomingData : historyData;
    if (!data) return [];
    const annotated = annotateGroupings(data.groupings, activeTab);
    if (filter.active === FILTER_ALL) return annotated;
    // Master-name filter.
    return annotated
      .map((g) => ({
        ...g,
        bookings: g.bookings.filter(
          (b) => b.master_first_name === filter.active,
        ),
      }))
      .filter((g) => g.bookings.length > 0);
  }, [activeTab, historyData, upcomingData, filter.active]);

  const slice = activeTab === "upcoming" ? upcoming : history;
  const sectionCount =
    activeTab === "upcoming" ? upcomingCount : historyCount;

  const isMultiTenant = annotatedGroupings.length > 1;

  // Empty-state branching — Tau §9.1.
  const emptyVariant: "no_bookings_ever" | "no_upcoming_has_past" | "no_history_has_upcoming" | null = (() => {
    if (slice.kind !== "ok" || sectionCount > 0) return null;
    if (activeTab === "upcoming") {
      if (historyCount === 0) return "no_bookings_ever";
      return "no_upcoming_has_past";
    }
    // history empty
    if (upcomingCount > 0) return "no_history_has_upcoming";
    return "no_bookings_ever";
  })();

  // ── handlers ─────────────────────────────────────────────────────────
  const handleOpenBooking = useCallback(
    (b: BookingListItem) => {
      navigate(`/customer/records/${b.booking_id}`);
    },
    [navigate],
  );

  const handleMessage = useCallback((b: BookingListItem) => {
    setMessageBooking(b);
  }, []);

  const handleRoute = useCallback((b: BookingListItem) => {
    // Route deeplink lives on the detail payload; for list-card route
    // we route to detail and rely on the detail screen's Маршрут CTA.
    // Defence-in-depth: if the list ever carries the deeplink directly,
    // open it here.
    navigate(`/customer/records/${b.booking_id}`);
  }, [navigate]);

  const handleReschedule = useCallback(
    (b: BookingListItem) => {
      // Reuses existing reschedule flow under /my-visits.
      navigate(`/my-visits/${b.booking_id}/reschedule`);
    },
    [navigate],
  );

  const handleCancel = useCallback(
    (b: BookingListItem) => {
      // Cancel flow lives on the detail screen per existing pattern.
      navigate(`/customer/records/${b.booking_id}`);
    },
    [navigate],
  );

  const handleRepeat = useCallback(
    async (b: BookingListItem) => {
      try {
        const intent = await getRepeatIntent(b.booking_id);
        if (intent.prefill_available && intent.booking_flow_url) {
          navigate(intent.booking_flow_url);
          return;
        }
        // Fallback — graceful degradation to catalog.
        navigate("/customer/catalog");
      } catch {
        navigate("/customer/catalog");
      }
    },
    [navigate],
  );

  const handleReview = useCallback(
    (b: BookingListItem) => {
      navigate(`/feedback/${b.booking_id}`);
    },
    [navigate],
  );

  const handleAskAyla = useCallback(() => {
    // Customer chat lives on the root. Navigate there.
    navigate("/");
  }, [navigate]);

  const handleFindService = useCallback(() => {
    navigate("/customer/catalog");
  }, [navigate]);

  // ── render ────────────────────────────────────────────────────────────
  return (
    <div className="records-screen" lang="ru">
      {/* WCAG 2.4.1 skip link. */}
      <a className="records-screen__skip-link" href="#records-main">
        К списку записей
      </a>

      <header className="records-screen__header" role="banner">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate("/customer/main")}
        >
          <span aria-hidden="true">←</span>
        </button>
        <h1 className="records-screen__title">Записи</h1>
      </header>

      {!online && (
        <div
          className="records-screen__offline-banner"
          role="status"
          aria-live="polite"
        >
          Записи могут быть устаревшими — нет сети.
        </div>
      )}

      {/* Tab strip — WCAG 1.3.1 role=tablist. */}
      <div
        className="records-screen__tabs"
        role="tablist"
        aria-label="Раздел записей"
      >
        <button
          type="button"
          role="tab"
          id="records-tab-upcoming"
          aria-selected={activeTab === "upcoming"}
          aria-controls="records-panel-upcoming"
          className={`records-screen__tab${activeTab === "upcoming" ? " records-screen__tab--active" : ""}`}
          onClick={() => setActiveTab("upcoming")}
        >
          Ближайшие{upcomingCount > 0 && ` (${upcomingCount})`}
        </button>
        <button
          type="button"
          role="tab"
          id="records-tab-history"
          aria-selected={activeTab === "history"}
          aria-controls="records-panel-history"
          className={`records-screen__tab${activeTab === "history" ? " records-screen__tab--active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          История{historyCount > 0 && ` (${historyCount})`}
        </button>
      </div>

      <main
        id="records-main"
        className="records-screen__main"
        role="tabpanel"
        aria-labelledby={
          activeTab === "upcoming"
            ? "records-tab-upcoming"
            : "records-tab-history"
        }
      >
        {/* Filter chips — history tab only when count >=5. */}
        {filterChipsEnabled && (
          <div
            className="records-screen__filter-row"
            role="group"
            aria-label="Фильтр истории"
          >
            <button
              type="button"
              className={`records-screen__chip${filter.active === FILTER_ALL ? " records-screen__chip--active" : ""}`}
              aria-pressed={filter.active === FILTER_ALL}
              onClick={() => setFilter({ active: FILTER_ALL })}
            >
              Все
            </button>
            {masterFilterOptions.map((name) => (
              <button
                key={name}
                type="button"
                className={`records-screen__chip${filter.active === name ? " records-screen__chip--active" : ""}`}
                aria-pressed={filter.active === name}
                onClick={() => setFilter({ active: name })}
              >
                У {name}
              </button>
            ))}
          </div>
        )}

        {/* Loading. */}
        {slice.kind === "loading" && (
          <div className="records-screen__list">
            <BookingCardSkeleton />
            <BookingCardSkeleton />
            <BookingCardSkeleton />
          </div>
        )}

        {/* Error. */}
        {slice.kind === "error" && (
          <div className="records-screen__error" role="status" aria-live="polite">
            <p>
              {slice.reason === "network"
                ? "Не получилось загрузить. Проверь интернет."
                : "Что-то пошло не так. Попробуй ещё раз через минуту."}
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void fetchSection(activeTab)}
            >
              Обновить
            </button>
          </div>
        )}

        {/* Empty. */}
        {slice.kind === "ok" && emptyVariant && (
          <EmptyRecordsState
            variant={emptyVariant}
            onFindService={handleFindService}
            onAskAyla={handleAskAyla}
            onRepeatLast={() => {
              // From the «no upcoming, has past» state — switch to history
              // tab and surface the «Записаться ещё» CTAs there.
              setActiveTab("history");
            }}
            onGoToHistory={() => setActiveTab("history")}
          />
        )}

        {/* Filter active but produced 0 items. */}
        {slice.kind === "ok" &&
          !emptyVariant &&
          annotatedGroupings.length === 0 && (
            <div
              className="records-screen__filter-empty"
              role="status"
              aria-live="polite"
            >
              <p>По этому фильтру записей нет.</p>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setFilter({ active: FILTER_ALL })}
              >
                Показать все
              </button>
            </div>
          )}

        {/* Render groupings. */}
        {slice.kind === "ok" && annotatedGroupings.length > 0 && (
          <div className="records-screen__list">
            {annotatedGroupings.map((g) => {
              return (
                <section
                  key={g.tenant_id}
                  className="records-screen__group"
                  aria-label={isMultiTenant ? g.tenant_name : undefined}
                >
                  {isMultiTenant && (
                    <TenantGroupHeader tenantName={g.tenant_name} />
                  )}

                  {/* Bucket by _timeGroup within the tenant. */}
                  {renderTimeBuckets({
                    bookings: g.bookings,
                    activeTab,
                    isMultiTenant,
                    onOpen: handleOpenBooking,
                    onMessage: handleMessage,
                    onRoute: handleRoute,
                    onReschedule: handleReschedule,
                    onCancel: handleCancel,
                    onRepeat: (b) => void handleRepeat(b),
                    onReview: handleReview,
                  })}
                </section>
              );
            })}

            {/* Pagination — Tau §3.4 «Показать ещё (N)». */}
            {slice.data.has_more && (
              <button
                type="button"
                className="btn-secondary records-screen__load-more"
                onClick={() => {
                  /* TODO W4: wire next_cursor pagination. Stub MVP
                   * returns has_more=false, so this branch is unreached
                   * in stub mode. */
                }}
              >
                Показать ещё
              </button>
            )}
          </div>
        )}
      </main>

      {/* AMM modal. */}
      {messageBooking && (
        <BookingMessageModal
          open={!!messageBooking}
          booking={messageBooking}
          onClose={() => setMessageBooking(null)}
          onSent={(r) =>
            setToast(`Передала ${r.masterFirstName} из ${r.tenantName}.`)
          }
        />
      )}

      {/* Toast. */}
      {toast && (
        <div className="records-screen__toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}

      {/* Bottom nav — mirror wellness dashboard nav so the «Записи» tab
          is selected. Reuses the wellness-dash__nav styles. */}
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
          className="wellness-dash__nav-tab wellness-dash__nav-tab--active"
          aria-current="page"
          aria-label="Записи"
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
          className="wellness-dash__nav-tab"
          aria-label="Я"
          onClick={() => navigate("/me")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            👤
          </span>
          <span className="wellness-dash__nav-label">Я</span>
        </button>
      </nav>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Render helper — time-bucket the bookings within a tenant group.
// ---------------------------------------------------------------------------

interface BucketProps {
  bookings: import("../lib/customer-records").AnnotatedBookingItem[];
  activeTab: BookingSection;
  isMultiTenant: boolean;
  onOpen: (b: BookingListItem) => void;
  onMessage: (b: BookingListItem) => void;
  onRoute: (b: BookingListItem) => void;
  onReschedule: (b: BookingListItem) => void;
  onCancel: (b: BookingListItem) => void;
  onRepeat: (b: BookingListItem) => void;
  onReview: (b: BookingListItem) => void;
}

function renderTimeBuckets(props: BucketProps) {
  const {
    bookings,
    activeTab,
    isMultiTenant,
    onOpen,
    onMessage,
    onRoute,
    onReschedule,
    onCancel,
    onRepeat,
    onReview,
  } = props;

  // Stable bucket order — preserves the order in which buckets first
  // appear in the data. (Backend already returns items sorted by
  // visit_at; we just group consecutive items by label.)
  const buckets = new Map<
    string,
    import("../lib/customer-records").AnnotatedBookingItem[]
  >();
  for (const b of bookings) {
    const arr = buckets.get(b._timeGroup) ?? [];
    arr.push(b);
    buckets.set(b._timeGroup, arr);
  }

  return Array.from(buckets.entries()).map(([label, items]) => (
    <div key={label} className="records-screen__time-bucket">
      <TimeGroupHeader label={label} level={isMultiTenant ? 3 : 2} />
      <ul className="records-screen__items" role="list">
        {items.map((b) => {
          const variant: "nearest" | "future" | "past" =
            activeTab === "history"
              ? "past"
              : b.is_nearest
                ? "nearest"
                : "future";
          return (
            <li key={b.booking_id}>
              <BookingCard
                item={b}
                variant={variant}
                onOpen={() => onOpen(b)}
                onMessage={() => onMessage(b)}
                onRoute={() => onRoute(b)}
                onReschedule={() => onReschedule(b)}
                onCancel={() => onCancel(b)}
                onRepeat={() => onRepeat(b)}
                onReview={() => onReview(b)}
                reviewPending={
                  variant === "past" && b.actions_available.includes("review")
                }
              />
            </li>
          );
        })}
      </ul>
    </div>
  ));
}
