/**
 * Master M5 conversation list — own conversations only, PII-stripped cards.
 *
 * Route: /master/conversations
 *
 * Spec source: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M5
 * (lines 556-630). State-matrix discipline per
 * docs/design/policies/customer-first-touch-and-mini-app-states.md §7.
 *
 * Spec quote (§M5, line 567-568):
 *
 *     «[Активные (2)] [Все] [Решённые]  Tabs
 *      🔎 [ Поиск по имени ]»
 *
 * Spec quote (§M5, lines 608-615):
 *
 *     «Master's card is **stripped down** vs admin's: ❌ No LTV / financial
 *      signal · ❌ No reveal-phone hint · … ✅ Customer first name only»
 *
 * Backend contract: GET /api/v1/master/conversations?filter&search&cursor&limit
 *
 * Bridge API (§M5 line 553 / implicit):
 *   - BackButton.show() (not root)
 *   - HapticFeedback.selectionChanged() on tab switch
 *   - Pull-to-refresh → HapticFeedback.impactOccurred('soft' approximated 'light')
 *
 * Tapping a card routes to M6 (`/master/conversations/:id`). Permission
 * cliff per spec line 623: a deep-linked non-own conversation surfaces
 * as 404 on M6 → M6 navigates back here with `state.permissionCliff =
 * true`, triggering the toast «Этот диалог не для вас».
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import {
  FORBIDDEN_PII_KEYS,
  findForbiddenPiiKeys,
  getMasterConversations,
  type ConversationFilter,
  type ConversationSection,
  type MasterConversationItem,
  type MasterConversationsResponse,
  type SlaTier,
} from "../lib/master-api";
import {
  hapticImpact,
  hapticSelection,
  setBackButton,
  signalReady,
} from "../lib/max-sdk";
import { MasterTabBar } from "../components/MasterTabBar";
import { Snackbar } from "../components/Snackbar";
import {
  formatRelativePast,
  joinClientName,
} from "../lib/masterDateFormat";

// --- Russian copy (VERBATIM from §M5) -------------------------------------

const COPY = {
  title: "Диалоги",
  tabs: {
    active: (n: number) => `Активные (${n})`,
    all: "Все",
    resolved: "Решённые",
  },
  searchPlaceholder: "Поиск по имени",
  sections: {
    awaiting_master: (n: number) =>
      `━━ ЖДУТ ВАШЕГО ОТВЕТА (${n}) ━━━━`,
    ai_drafted: (n: number) => `━━ ПРЕДЛОЖЕН ОТВЕТ (${n}) ━━━━━━━`,
    ai_handling: (n: number) => `━━ ИДЁТ ДИАЛОГ (${n}) ━━━━━━━━━━━`,
    resolved: (n: number) => `━━ РЕШЕНО СЕГОДНЯ (${n}) ━━━━━━━━`,
    other: (n: number) => `━━ ПРОЧЕЕ (${n}) ━━━━━━━━━━━`,
  },
  returning: "постоянный клиент",
  emptyActive: "Все ваши клиенты сейчас довольны. Спасибо за работу!",
  emptyAll: "Здесь будут диалоги с вашими клиентами.",
  emptyResolved: "Здесь будут решённые диалоги.",
  loading: "Загружаем диалоги…",
  errorTitle: "Не получилось загрузить",
  retry: "Попробовать снова",
  loadMore: "Показать ещё",
  noResults: (q: string) => `Ничего не найдено по запросу «${q}»`,
  toastPermissionCliff: "Этот диалог не для вас",
  searchAria: "Поиск по имени клиента",
};

// --- View state -----------------------------------------------------------

type Phase =
  | { kind: "loading" }
  | {
      kind: "ready";
      data: MasterConversationsResponse;
      appending?: boolean;
    }
  | { kind: "error_initial"; err: unknown };

const SEARCH_DEBOUNCE_MS = 220;

// --- Component ------------------------------------------------------------

export function MasterConversationsScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const [filter, setFilter] = useState<ConversationFilter>("active");
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [toast, setToast] = useState<{ visible: boolean; message: string }>(
    { visible: false, message: "" },
  );

  // BackButton: NOT root, so show.
  useEffect(() => {
    setBackButton(true);
    signalReady();
    return () => setBackButton(false);
  }, []);

  // Permission-cliff: spec §M5 line 623. The Mini App router sends users
  // who hit a forbidden deeplink (e.g. /master/conversations/<other-id>)
  // back to the list with a `state.permissionCliff = true` flag. M6 isn't
  // built yet but we honor the flag now so the toast plumbing is ready.
  useEffect(() => {
    const state = location.state as { permissionCliff?: boolean } | null;
    if (state?.permissionCliff) {
      setToast({ visible: true, message: COPY.toastPermissionCliff });
      // Clear the flag so refresh doesn't re-fire.
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate]);

  // Debounce search input — avoids hitting the API on every keystroke.
  useEffect(() => {
    const id = window.setTimeout(
      () => setDebouncedSearch(searchInput.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(id);
  }, [searchInput]);

  // --- Data load -------------------------------------------------------

  const load = useCallback(
    async (cursor?: string) => {
      if (!cursor) setPhase({ kind: "loading" });
      else
        setPhase((prev) =>
          prev.kind === "ready"
            ? { kind: "ready", data: prev.data, appending: true }
            : prev,
        );
      try {
        const params: {
          filter: ConversationFilter;
          search?: string;
          cursor?: string;
          limit: number;
        } = {
          filter,
          limit: 25,
        };
        if (debouncedSearch) params.search = debouncedSearch;
        if (cursor) params.cursor = cursor;
        const data = await getMasterConversations(params);
        runPiiSafetyCheck(data.items);
        if (cursor) {
          setPhase((prev) => {
            if (prev.kind !== "ready") return { kind: "ready", data };
            return {
              kind: "ready",
              data: {
                items: [...prev.data.items, ...data.items],
                section_counts: data.section_counts,
                next_cursor: data.next_cursor,
              },
            };
          });
        } else {
          setPhase({ kind: "ready", data });
        }
      } catch (err) {
        if (cursor) {
          // Pagination failure — keep current data, just stop appending.
          console.warn("[conversations] paginate failed", err);
          setPhase((prev) =>
            prev.kind === "ready"
              ? { kind: "ready", data: prev.data, appending: false }
              : prev,
          );
          return;
        }
        setPhase({ kind: "error_initial", err });
      }
    },
    [filter, debouncedSearch],
  );

  useEffect(() => {
    void load();
    // load() depends on filter + debouncedSearch via useCallback closure.
  }, [load]);

  // --- Pull-to-refresh ------------------------------------------------

  const touchStartY = useRef<number | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const el = scrollContainerRef.current;
    if (!el || el.scrollTop > 0) {
      touchStartY.current = null;
      return;
    }
    touchStartY.current = e.touches[0]?.clientY ?? null;
  }, []);

  const onTouchEnd = useCallback(
    (e: React.TouchEvent<HTMLDivElement>) => {
      const start = touchStartY.current;
      touchStartY.current = null;
      if (start === null) return;
      const endY = e.changedTouches[0]?.clientY ?? start;
      // Spec §M5 (Bridge API): "soft" impact for pull-to-refresh. MAX
      // bridge has no 'soft' style; closest is 'light'.
      if (endY - start >= 60) {
        hapticImpact("light");
        void load();
      }
    },
    [load],
  );

  // --- Card tap → M6 conversation detail ------------------------------

  const onCardTap = useCallback(
    (item: MasterConversationItem) => {
      hapticSelection();
      navigate(`/master/conversations/${item.conversation_id}`);
    },
    [navigate],
  );

  // --- Tab change -----------------------------------------------------

  const onTabChange = useCallback((f: ConversationFilter) => {
    hapticSelection();
    setFilter(f);
  }, []);

  // --- Render ---------------------------------------------------------

  return (
    <>
      <div
        ref={scrollContainerRef}
        className="master-dashboard"
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        <ConversationsHeader />
        <Tabs
          filter={filter}
          onChange={onTabChange}
          activeCount={
            phase.kind === "ready"
              ? phase.data.section_counts.awaiting_master +
                phase.data.section_counts.ai_drafted
              : 0
          }
        />
        <SearchBox
          value={searchInput}
          onChange={setSearchInput}
        />
        <Body
          phase={phase}
          filter={filter}
          searchQuery={debouncedSearch}
          onRetry={() => void load()}
          onLoadMore={(cursor) => void load(cursor)}
          onCardTap={onCardTap}
        />
      </div>
      <Snackbar
        visible={toast.visible}
        message={toast.message}
        durationMs={3500}
        onTimeout={() => setToast({ visible: false, message: "" })}
      />
      <MasterTabBar
        unreadCount={
          phase.kind === "ready"
            ? phase.data.section_counts.awaiting_master
            : 0
        }
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />
    </>
  );
}

// --- PII safety check ---------------------------------------------------

function runPiiSafetyCheck(items: MasterConversationItem[]): void {
  // Defensive — the backend already strips PII before responding (spec
  // §M5 lines 608-615). This is defence-in-depth: if a regression sneaks
  // through and the response contains phone / LTV / email / full last
  // name, we log a console warning. We DO NOT throw — backend is the
  // authority. The screen continues to render whatever made it through.
  for (const item of items) {
    const observed = findForbiddenPiiKeys(
      item as unknown as Record<string, unknown>,
    );
    if (observed.length > 0) {
      console.warn(
        "[conversations] PII safety check failed — backend leaked",
        observed,
        "in",
        item.conversation_id,
        "; allowed forbidden keys list:",
        FORBIDDEN_PII_KEYS,
      );
    }
  }
}

// --- Header --------------------------------------------------------------

function ConversationsHeader() {
  return (
    <header className="schedule-header">
      <div className="schedule-header__top">
        <h1 className="screen__title schedule-header__title">
          {COPY.title}
        </h1>
      </div>
    </header>
  );
}

// --- Tabs ---------------------------------------------------------------

function Tabs({
  filter,
  onChange,
  activeCount,
}: {
  filter: ConversationFilter;
  onChange: (f: ConversationFilter) => void;
  activeCount: number;
}) {
  return (
    <div
      className="schedule-segments"
      role="tablist"
      aria-label="Фильтр диалогов"
    >
      {([
        ["active", COPY.tabs.active(activeCount)],
        ["all", COPY.tabs.all],
        ["resolved", COPY.tabs.resolved],
      ] as const).map(([key, label]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={filter === key}
          className={`schedule-segments__tab${filter === key ? " schedule-segments__tab--active" : ""}`}
          onClick={() => onChange(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// --- Search bar ---------------------------------------------------------

function SearchBox({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="schedule-search">
      <span className="schedule-search__icon" aria-hidden="true">
        🔎
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        placeholder={COPY.searchPlaceholder}
        aria-label={COPY.searchAria}
        className="schedule-search__input"
      />
    </div>
  );
}

// --- Body dispatch ------------------------------------------------------

function Body({
  phase,
  filter,
  searchQuery,
  onRetry,
  onLoadMore,
  onCardTap,
}: {
  phase: Phase;
  filter: ConversationFilter;
  searchQuery: string;
  onRetry: () => void;
  onLoadMore: (cursor: string) => void;
  onCardTap: (item: MasterConversationItem) => void;
}) {
  if (phase.kind === "loading") return <LoadingSkeleton />;
  if (phase.kind === "error_initial")
    return <ErrorInitial err={phase.err} onRetry={onRetry} />;
  const { data } = phase;
  if (data.items.length === 0) {
    if (searchQuery)
      return (
        <section className="master-dashboard__section">
          <p className="master-dashboard__empty-line">
            {COPY.noResults(searchQuery)}
          </p>
        </section>
      );
    return (
      <section className="master-dashboard__section">
        <p className="master-dashboard__empty-line">
          {filter === "active"
            ? COPY.emptyActive
            : filter === "resolved"
              ? COPY.emptyResolved
              : COPY.emptyAll}
        </p>
      </section>
    );
  }

  return (
    <>
      <Sections items={data.items} onCardTap={onCardTap} filter={filter} />
      {data.next_cursor ? (
        <div className="master-dashboard__section">
          <button
            type="button"
            className="btn-secondary master-dashboard__inline-cta"
            onClick={() =>
              data.next_cursor && onLoadMore(data.next_cursor)
            }
            disabled={phase.appending === true}
          >
            {COPY.loadMore}
          </button>
        </div>
      ) : null}
    </>
  );
}

// --- Sectioned list -----------------------------------------------------

const SECTION_ORDER: ConversationSection[] = [
  "awaiting_master",
  "ai_drafted",
  "ai_handling",
  "resolved",
  "other",
];

function Sections({
  items,
  onCardTap,
  filter,
}: {
  items: MasterConversationItem[];
  onCardTap: (i: MasterConversationItem) => void;
  filter: ConversationFilter;
}) {
  const grouped = useMemo(() => {
    const out = new Map<ConversationSection, MasterConversationItem[]>();
    for (const item of items) {
      const sec = (item.section as ConversationSection) ?? "other";
      const bucket = out.get(sec) ?? [];
      bucket.push(item);
      out.set(sec, bucket);
    }
    return out;
  }, [items]);

  return (
    <>
      {SECTION_ORDER.map((section) => {
        const rows = grouped.get(section) ?? [];
        if (rows.length === 0) return null;
        // Per spec lines 569-592, "РЕШЕНО СЕГОДНЯ" only appears when the
        // resolved filter is selected or "Все" — backend includes
        // resolved rows in `all` and `resolved`. We hide the section when
        // the active filter is selected (would be empty anyway).
        if (section === "resolved" && filter === "active") return null;
        return (
          <section
            key={section}
            className="master-dashboard__section"
            aria-labelledby={`m5-${section}`}
          >
            <h2
              className="master-dashboard__section-title"
              id={`m5-${section}`}
            >
              {COPY.sections[section](rows.length)}
            </h2>
            {rows.map((item) => (
              <ConversationCard
                key={item.conversation_id}
                item={item}
                onTap={onCardTap}
              />
            ))}
          </section>
        );
      })}
    </>
  );
}

// --- Card --------------------------------------------------------------

function ConversationCard({
  item,
  onTap,
}: {
  item: MasterConversationItem;
  onTap: (i: MasterConversationItem) => void;
}) {
  const clientName = joinClientName(
    item.client_first_name,
    item.client_last_initial,
  );
  const tier: SlaTier = item.sla_tier;
  const isResolved =
    item.section === "resolved" && item.resolved_outcome !== null;
  return (
    <button
      type="button"
      className="m-card m-card--tappable m-card--inbox"
      onClick={() => onTap(item)}
    >
      <div className="m-card__title">
        <span
          className={`m-card__dot m-card__dot--${tier}`}
          aria-label={`SLA: ${tier}`}
        />
        <span>{clientName}</span>
        {item.last_message_at ? (
          <span className="m-card__meta-inline">
            · {formatRelativePast(item.last_message_at)}
          </span>
        ) : null}
      </div>
      {item.is_returning_customer ? (
        <div className="m-card__chip m-card__chip--warning">
          {COPY.returning}
        </div>
      ) : null}
      {item.reason_chip ? (
        <div className="m-card__chip m-card__chip--warning">
          {item.reason_chip}
        </div>
      ) : null}
      {isResolved && item.resolved_outcome ? (
        <div className="m-card__hint">→ {item.resolved_outcome}</div>
      ) : item.last_message_excerpt ? (
        <div className="m-card__excerpt">
          «{item.last_message_excerpt}»
        </div>
      ) : null}
    </button>
  );
}

// --- Loading / error ---------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="master-dashboard__skeleton-wrap" aria-busy="true">
      <p className="master-dashboard__loading-label">{COPY.loading}</p>
      {[0, 1, 2, 3].map((i) => (
        <div className="m-card m-card--skel" key={i}>
          <div className="skeleton" style={{ width: "55%", height: "1.1em" }} />
          <div
            className="skeleton"
            style={{ width: "80%", height: "0.9em", marginTop: 8 }}
          />
        </div>
      ))}
    </div>
  );
}

function ErrorInitial({
  err,
  onRetry,
}: {
  err: unknown;
  onRetry: () => void;
}) {
  const isServer = err instanceof ApiError && err.status >= 500;
  const body = isServer
    ? "Что-то у нас не получается прямо сейчас."
    : "Не получилось загрузить. Проверьте интернет и попробуйте снова.";
  return (
    <div className="master-dashboard__section">
      <h2 className="master-dashboard__section-title">{COPY.errorTitle}</h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{body}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {COPY.retry}
          </button>
        </div>
      </div>
    </div>
  );
}

// Unused exported type-helper to keep RefObject availability obvious if
// future PRs add a scrollable virtual list.
export type _ScrollRefHelper = RefObject<HTMLDivElement>;
