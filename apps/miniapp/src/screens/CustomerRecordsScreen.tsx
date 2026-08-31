/**
 * Customer records screen — REAL bookings (pilot phase 3.2).
 *
 * Spec: `docs/screens/customer-records-flow.md` §3 (R1 main records
 * list) + §7 (R5 empty states) + §9 (states matrix).
 *
 * Phase 3.2 (stub removal, orchestrator GO): data comes from the real
 * `GET /bookings/list` (upcoming / `?status=past` history) via
 * `lib/customer-records.ts`. Gone with the stub: multi-tenant groupings
 * (single-tenant pilot — the field doesn't exist upstream), «Сообщить
 * по записи» AMM modal (no endpoint), repeat-intent prefill (no
 * endpoint — «Записаться ещё» opens the real catalog), invented
 * prices/addresses. Home = records: this screen is the tab root at
 * `/customer/main` (also mounted at `/customer/records`).
 *
 * # Tab structure (unchanged)
 *
 * Two top-level tabs «Ближайшие (N)» / «История (N)», default
 * «Ближайшие». Filter chips on «История» when total past >= 5 (Tau
 * §13 implementer note #8) — unique masters from the loaded data.
 *
 * # 5-state matrix (Wellness pattern reuse)
 *
 *   loading  → 3 skeleton cards
 *   ok       → render time buckets
 *   empty    → EmptyRecordsState
 *   error    → generic copy + retry
 *   offline  → banner «Записи могут быть устаревшими»
 *
 * # Вход в анкету цели (решение владельца 30.08)
 *
 * Под содержимым экрана — `GoalInviteCard`. Показывается по серверному
 * `decision-context.missing`, не по эвристике «нет записей»; секция, а
 * не модалка и не шаг мастера, так что дорога к записи не перекрыта.
 * Порядок нормативный: BOT-001 §13 — First Contact не начинается с
 * анкеты, а Mini App entry входит в область действия BOT-001 (§2.1).
 *
 * # Voice locks
 *
 *   - «Записи» (NOT «Bookings»), «ты» canonical, no exclamation marks
 *
 * # WCAG (§13 inline)
 *
 *   - Tab strip uses role="tablist" / role="tab" / aria-controls.
 *   - Skip link «К списку записей» so SR users can bypass the header.
 *   - Time group headers use heading semantics.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookingCard, BookingCardSkeleton } from "../components/BookingCard";
import { EmptyRecordsState } from "../components/EmptyRecordsState";
import { GoalInviteCard } from "../components/GoalInviteCard";
import { TimeGroupHeader } from "../components/TimeGroupHeader";
import { ApiError } from "../lib/api";
import {
  annotateItems,
  getMyBookings,
  type AnnotatedRecordItem,
  type BookingSection,
  type RecordItem,
  type RecordsPage,
} from "../lib/customer-records";

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

const FILTER_ALL = "__all__";

// ---------------------------------------------------------------------------
// Main screen.
// ---------------------------------------------------------------------------

export function CustomerRecordsScreen() {
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<BookingSection>("upcoming");
  const [upcoming, setUpcoming] = useState<Slice<RecordsPage>>({
    kind: "loading",
  });
  const [history, setHistory] = useState<Slice<RecordsPage>>({
    kind: "loading",
  });
  const [online, setOnline] = useState<boolean>(isOnline());
  const [filter, setFilter] = useState<{ active: string }>({ active: FILTER_ALL });
  const [loadingMore, setLoadingMore] = useState(false);

  // ── data fetch ────────────────────────────────────────────────────────
  const fetchSection = useCallback(async (section: BookingSection) => {
    const setSlice = section === "upcoming" ? setUpcoming : setHistory;
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

  // «Показать ещё» — real cursor pagination (`before` = next_cursor).
  const loadMore = useCallback(async () => {
    const slice = activeTab === "upcoming" ? upcoming : history;
    if (slice.kind !== "ok" || !slice.data.nextCursor) return;
    const cursor = slice.data.nextCursor;
    setLoadingMore(true);
    try {
      const page = await getMyBookings(activeTab, cursor);
      const setSlice = activeTab === "upcoming" ? setUpcoming : setHistory;
      setSlice((s) =>
        s.kind === "ok"
          ? {
              kind: "ok",
              data: {
                ...s.data,
                items: [...s.data.items, ...page.items],
                totalCount: s.data.totalCount + page.items.length,
                nextCursor: page.nextCursor,
              },
            }
          : s,
      );
    } catch {
      /* load-more failure is silent — the button stays for a retry */
    } finally {
      setLoadingMore(false);
    }
  }, [activeTab, upcoming, history]);

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

  // ── derived state ────────────────────────────────────────────────────
  const upcomingData = upcoming.kind === "ok" ? upcoming.data : null;
  const historyData = history.kind === "ok" ? history.data : null;

  const upcomingCount = upcomingData?.totalCount ?? 0;
  const historyCount = historyData?.totalCount ?? 0;

  // Filter chip eligibility — Tau §13 #8: show only when history has >=5.
  const filterChipsEnabled = activeTab === "history" && historyCount >= 5;

  // Unique master names from the loaded history — no hardcoded list.
  const masterFilterOptions = useMemo(() => {
    if (!filterChipsEnabled || !historyData) return [];
    const seen = new Set<string>();
    const ordered: string[] = [];
    for (const b of historyData.items) {
      if (!seen.has(b.masterName)) {
        seen.add(b.masterName);
        ordered.push(b.masterName);
      }
    }
    return ordered;
  }, [filterChipsEnabled, historyData]);

  // Apply the master filter to the active section's items.
  const annotatedItems: AnnotatedRecordItem[] = useMemo(() => {
    const data = activeTab === "upcoming" ? upcomingData : historyData;
    if (!data) return [];
    const items =
      filter.active === FILTER_ALL
        ? data.items
        : data.items.filter((b) => b.masterName === filter.active);
    return annotateItems(items, activeTab);
  }, [activeTab, historyData, upcomingData, filter.active]);

  const slice = activeTab === "upcoming" ? upcoming : history;
  const sectionCount = activeTab === "upcoming" ? upcomingCount : historyCount;

  // Empty-state branching — Tau §9.1.
  const emptyVariant:
    | "no_bookings_ever"
    | "no_upcoming_has_past"
    | "no_history_has_upcoming"
    | null = (() => {
    if (slice.kind !== "ok" || sectionCount > 0) return null;
    if (activeTab === "upcoming") {
      if (historyCount === 0) return "no_bookings_ever";
      return "no_upcoming_has_past";
    }
    if (upcomingCount > 0) return "no_history_has_upcoming";
    return "no_bookings_ever";
  })();

  // ── handlers ─────────────────────────────────────────────────────────
  const handleOpenBooking = useCallback(
    (b: RecordItem) => {
      navigate(`/customer/records/${b.bookingId}`);
    },
    [navigate],
  );

  const handleReschedule = useCallback(
    (b: RecordItem) => {
      // Real reschedule flow (existing screen, /my-visits namespace).
      navigate(`/my-visits/${b.bookingId}/reschedule`);
    },
    [navigate],
  );

  const handleCancel = useCallback(
    (b: RecordItem) => {
      // Cancel flow lives on the detail screen (real 2-step + undo).
      navigate(`/customer/records/${b.bookingId}`);
    },
    [navigate],
  );

  const handleRepeat = useCallback(() => {
    // No prefill endpoint in the pilot — the real catalog is the honest
    // repeat path (phase 3.1 made it real).
    navigate("/customer/catalog");
  }, [navigate]);

  const handleReview = useCallback(
    (b: RecordItem) => {
      navigate(`/feedback/${b.bookingId}`);
    },
    [navigate],
  );

  const handleAskAyla = useCallback(() => {
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
            onRepeatLast={() => setActiveTab("history")}
            onGoToHistory={() => setActiveTab("history")}
          />
        )}

        {/* Filter active but produced 0 items. */}
        {slice.kind === "ok" &&
          !emptyVariant &&
          annotatedItems.length === 0 && (
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

        {/* Render time buckets. */}
        {slice.kind === "ok" && annotatedItems.length > 0 && (
          <div className="records-screen__list">
            {renderTimeBuckets({
              items: annotatedItems,
              activeTab,
              onOpen: handleOpenBooking,
              onReschedule: handleReschedule,
              onCancel: handleCancel,
              onRepeat: handleRepeat,
              onReview: handleReview,
            })}

            {/* Real cursor pagination. */}
            {slice.data.nextCursor && (
              <button
                type="button"
                className="btn-secondary records-screen__load-more"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "Загружаю…" : "Показать ещё"}
              </button>
            )}
          </div>
        )}
      </main>

      {/* Вход в анкету цели — ПОСЛЕ содержимого экрана, не до него.
          Показывается ровно тогда, когда сервер сказал, что чего-то не
          хватает (decision-context.missing), а не по эвристике «нет
          записей». Порядок здесь нормативный, а не косметический:
          BOT-001 §13 «First Contact MUST NOT begin with a standalone
          questionnaire», а Mini App entry — в области действия
          BOT-001 (§2.1). Экран начинается с того, зачем человек
          пришёл: записи или «Найти услугу». У нового человека список
          пуст, так что приглашение всё равно оказывается на первом
          же экране без прокрутки. */}
      <GoalInviteCard />

      {/* Bottom nav — records is home, the «Записи» tab is selected. */}
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
          onClick={() => navigate("/customer/profile")}
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
// Render helper — time-bucket the items.
// ---------------------------------------------------------------------------

interface BucketProps {
  items: AnnotatedRecordItem[];
  activeTab: BookingSection;
  onOpen: (b: RecordItem) => void;
  onReschedule: (b: RecordItem) => void;
  onCancel: (b: RecordItem) => void;
  onRepeat: () => void;
  onReview: (b: RecordItem) => void;
}

function renderTimeBuckets(props: BucketProps) {
  const { items, activeTab, onOpen, onReschedule, onCancel, onRepeat, onReview } =
    props;

  // Stable bucket order — preserves the order in which buckets first
  // appear in the data (backend already sorted rows by visit_at).
  const buckets = new Map<string, AnnotatedRecordItem[]>();
  for (const b of items) {
    const arr = buckets.get(b._timeGroup) ?? [];
    arr.push(b);
    buckets.set(b._timeGroup, arr);
  }

  return Array.from(buckets.entries()).map(([label, bucketItems]) => (
    <div key={label} className="records-screen__time-bucket">
      <TimeGroupHeader label={label} level={2} />
      <ul className="records-screen__items" role="list">
        {bucketItems.map((b) => {
          const variant: "nearest" | "future" | "past" =
            activeTab === "history"
              ? "past"
              : b.isNearest
                ? "nearest"
                : "future";
          return (
            <li key={b.bookingId}>
              <BookingCard
                item={b}
                variant={variant}
                onOpen={() => onOpen(b)}
                onReschedule={() => onReschedule(b)}
                onCancel={() => onCancel(b)}
                onRepeat={onRepeat}
                onReview={() => onReview(b)}
              />
            </li>
          );
        })}
      </ul>
    </div>
  ));
}
