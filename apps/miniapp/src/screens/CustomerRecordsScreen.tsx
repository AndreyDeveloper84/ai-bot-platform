/**
 * «Записи» клиента — список. Макет «Записи.png» (набор владельца от
 * 06.10.2026, DRF-2952), кадры 1, 2, 12, 14–16.
 *
 * # Что на экране
 *
 *   - шапка «Ayla ✦» и дверь в профиль; `<h1>Записи</h1>` остаётся для
 *     читающих с экрана — на макете заголовка нет, раздел называет вкладка
 *     нижней панели;
 *   - сегмент «Предстоящие | Прошедшие»;
 *   - «Предстоящие»: раздел «Предстоящие записи», под ним — «Прошедшие
 *     записи» (кадр 1): три самые свежие и кнопка «Все прошедшие»;
 *   - «Прошедшие»: список; у самой свежей записи — «Повторить запись» и
 *     «Посмотреть детали» (кадр 12);
 *   - записей нет вовсе — кадр 2, без сегмента.
 *
 * # Что осталось, хотя на макете этого нет
 *
 * Правило DRF-2948: работающую возможность нельзя убрать молча.
 *
 *   - `GoalInviteCard` первым (решение владельца 31.08) — показывается по
 *     серверному `decision-context.missing`, не по «нет записей»;
 *   - полоса «нет сети» и выключенные вместе с ней действия;
 *   - настоящая постраничная загрузка обоих разделов;
 *   - фильтр прошедших по мастеру, когда записей пять и больше;
 *   - «Оценить визит» — у свежей записи кнопкой, у остальных строкой на
 *     карточке; сама оценка — в карточке записи;
 *   - статус и оплата на карточке, когда они не обычные.
 *
 * # Что ушло
 *
 * Заголовки «Сегодня / Завтра / На этой неделе» и месяцы в истории (дата
 * стоит на карточке, год дописывается, когда он не текущий), счётчики во
 * вкладках (это было число загруженных, а не всех), кнопки «Перенести» и
 * «Отменить» на ближайшей карточке — действия живут в карточке записи.
 *
 * # Корень вкладки
 *
 * Экран — корень клиентской поверхности: нижняя панель стоит вне веток
 * состояний (DRF-2191 — отказ ручки не отнимает выход), стрелки «назад» нет.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CustomerAvatarEntry } from "../components/CustomerAvatarEntry";
import { CustomerTabBar } from "../components/CustomerTabBar";
import { GoalInviteCard } from "../components/GoalInviteCard";
import { RecordCard, RecordCardSkeleton } from "../components/records/RecordCard";
import { RecordsLoadError } from "../components/records/RecordsLoadError";
import { RecordButton, RecordsState } from "../components/records/RecordsState";
import { useOnline } from "../hooks/useOnline";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  getMyBookings,
  type BookingSection,
  type RecordItem,
  type RecordsPage,
} from "../lib/customer-records";
import { screenRoot } from "../lib/screen-back";

type Slice =
  | { kind: "loading" }
  | { kind: "ok"; data: RecordsPage }
  | { kind: "error"; err: unknown };

const FILTER_ALL = "__all__";
/** Сколько прошедших показывает вкладка «Предстоящие» (кадр 1). */
const HISTORY_PREVIEW = 3;
/** Фильтр по мастеру — когда прошедших набралось столько. */
const FILTER_FROM = 5;
/** Причина выключенной кнопки — одна на все. */
const OFFLINE_HINT = "Нет сети";

export const RECORDS_COPY = {
  tabUpcoming: "Предстоящие",
  tabHistory: "Прошедшие",
  upcomingHeading: "Предстоящие записи",
  historyHeading: "Прошедшие записи",
  emptyTitle: "У тебя пока нет записей",
  emptyBody: "Найди подходящего мастера и запишись на первую процедуру.",
  emptyBodyAyla: "поможет с выбором.",
  findMaster: "Найти мастера",
  noUpcoming: "Предстоящих записей нет.",
  noHistory: "История накопится после первых визитов.",
  allHistory: "Все прошедшие",
  repeat: "Повторить запись",
  details: "Посмотреть детали",
  rate: "Оценить визит",
  loadFailed: "Не удалось загрузить записи",
  more: "Показать ещё",
  moreBusy: "Загружаю…",
  filterEmpty: "По этому фильтру записей нет.",
  filterReset: "Показать все",
  offline:
    "Записи могут быть устаревшими — нет сети. Перенос, отмена и новая запись сейчас недоступны.",
} as const;

export function CustomerRecordsScreen() {
  const navigate = useNavigate();

  // Вид экрана (DRF-1493): корень. У «Записей» собственная вкладка нижней
  // панели — выход отсюда через соседнюю вкладку, а не «назад». Объявление
  // обязательно и здесь: кто рисует панель, тот объявил корень (DRF-2201).
  useScreenBack(
    screenRoot(
      "«Записи» — корневая вкладка клиентской поверхности; выход отсюда через нижнюю навигацию.",
    ),
  );

  const [activeTab, setActiveTab] = useState<BookingSection>("upcoming");
  const [upcoming, setUpcoming] = useState<Slice>({ kind: "loading" });
  const [history, setHistory] = useState<Slice>({ kind: "loading" });
  const [filter, setFilter] = useState<string>(FILTER_ALL);
  // Догрузка — у каждого раздела своя: общий признак включал бы кнопку
  // одного раздела по завершении запроса другого.
  const [loadingMore, setLoadingMore] = useState<Record<BookingSection, boolean>>({
    upcoming: false,
    history: false,
  });
  const online = useOnline();

  const fetchSection = useCallback(async (section: BookingSection) => {
    const setSlice = section === "upcoming" ? setUpcoming : setHistory;
    setSlice({ kind: "loading" });
    try {
      setSlice({ kind: "ok", data: await getMyBookings(section) });
    } catch (err) {
      setSlice({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void fetchSection("upcoming");
    void fetchSection("history");
  }, [fetchSection]);

  // «Показать ещё» — настоящая постраничная загрузка (`before` = next_cursor).
  const loadMore = useCallback(
    async (section: BookingSection) => {
      const slice = section === "upcoming" ? upcoming : history;
      if (slice.kind !== "ok" || !slice.data.nextCursor) return;
      setLoadingMore((m) => ({ ...m, [section]: true }));
      try {
        const page = await getMyBookings(section, slice.data.nextCursor);
        const setSlice = section === "upcoming" ? setUpcoming : setHistory;
        setSlice((s) => {
          if (s.kind !== "ok") return s;
          // Строка, которая уже показана, второй раз не добавляется: сервер
          // на повторном курсоре может вернуть те же записи.
          const seen = new Set(s.data.items.map((b) => b.bookingId));
          const fresh = page.items.filter((b) => !seen.has(b.bookingId));
          return {
            kind: "ok",
            data: {
              ...s.data,
              items: [...s.data.items, ...fresh],
              // Ничего нового не пришло — дальше листать нечего.
              nextCursor: fresh.length > 0 ? page.nextCursor : null,
            },
          };
        });
      } catch {
        /* тихо: кнопка остаётся, повтор — следующим нажатием */
      } finally {
        setLoadingMore((m) => ({ ...m, [section]: false }));
      }
    },
    [upcoming, history],
  );

  useEffect(() => {
    setFilter(FILTER_ALL);
  }, [activeTab]);

  const upcomingItems = useMemo(
    () => (upcoming.kind === "ok" ? upcoming.data.items : []),
    [upcoming],
  );
  const historyItems = useMemo(
    () => (history.kind === "ok" ? history.data.items : []),
    [history],
  );

  // Кадр 2 законен, только когда ОБА раздела прочитаны и пусты: пока история
  // грузится или упала, «записей нет» было бы догадкой.
  const nothingAtAll =
    upcoming.kind === "ok" &&
    history.kind === "ok" &&
    upcomingItems.length === 0 &&
    historyItems.length === 0;

  // Оба раздела не прочитаны — это кадры 14–16 целиком, без сегмента:
  // переключаться не между чем.
  const nothingLoaded = upcoming.kind === "error" && history.kind === "error";

  const filterEnabled = historyItems.length >= FILTER_FROM;
  const masterOptions = useMemo(() => {
    if (!filterEnabled) return [];
    return Array.from(new Set(historyItems.map((b) => b.masterName).filter(Boolean)));
  }, [filterEnabled, historyItems]);
  const shownHistory = useMemo(
    () =>
      filter === FILTER_ALL ? historyItems : historyItems.filter((b) => b.masterName === filter),
    [filter, historyItems],
  );

  const open = useCallback(
    (b: RecordItem) => navigate(`/customer/records/${b.bookingId}`),
    [navigate],
  );
  // Готового «повтора» у сервера нет: настоящий путь — каталог.
  const repeat = useCallback(() => navigate("/customer/catalog"), [navigate]);
  const rate = useCallback((b: RecordItem) => navigate(`/feedback/${b.bookingId}`), [navigate]);
  const findMaster = useCallback(() => navigate("/customer/catalog"), [navigate]);
  const askAyla = useCallback(() => navigate("/customer/ayla"), [navigate]);

  const loadMoreButton = (section: BookingSection, slice: Slice) =>
    slice.kind === "ok" &&
    slice.data.nextCursor && (
      <div className="cr-more">
        <RecordButton
          variant="soft"
          onClick={() => void loadMore(section)}
          disabled={loadingMore[section]}
        >
          {loadingMore[section] ? RECORDS_COPY.moreBusy : RECORDS_COPY.more}
        </RecordButton>
      </div>
    );

  const skeletons = (
    <div className="cr-list" aria-hidden="true">
      <RecordCardSkeleton />
      <RecordCardSkeleton />
      <RecordCardSkeleton />
    </div>
  );

  return (
    <div className="records-screen cr-screen" lang="ru">
      {/* WCAG 2.4.1 — мимо шапки сразу к списку. */}
      <a className="records-screen__skip-link" href="#records-main">
        К списку записей
      </a>

      <header className="cr-brandbar" role="banner">
        <h1 className="visually-hidden">Записи</h1>
        <span className="cr-brand" aria-hidden="true">
          <span lang="en">Ayla</span>
          <svg className="cr-brand__star" width="18" height="18" viewBox="0 0 24 24">
            <path d="M12 1.5c.9 5.6 4.9 9.6 10.5 10.5-5.6.9-9.6 4.9-10.5 10.5-.9-5.6-4.9-9.6-10.5-10.5 5.6-.9 9.6-4.9 10.5-10.5z" />
          </svg>
        </span>
        {/* Вход в профиль — §77 п.60: стоит на всех экранах нижней панели. */}
        <CustomerAvatarEntry />
      </header>

      {/* Полоса говорит «показанное могло устареть». Когда не загрузилось
          ничего, устаревать нечему — тогда говорит сам кадр «нет сети». */}
      {!online && !nothingLoaded && (
        <div className="records-screen__offline-banner" role="status" aria-live="polite">
          {RECORDS_COPY.offline}
        </div>
      )}

      {/* Приглашение к цели — ПЕРВЫМ, над записями (решение владельца 31.08
          поверх BOT-001 §13). Ничего не требует и ничего не загораживает. */}
      <GoalInviteCard />

      <main id="records-main" className="cr-main">
        {nothingLoaded ? (
          <RecordsLoadError
            err={upcoming.err}
            online={online}
            title={RECORDS_COPY.loadFailed}
            onRetry={() => {
              void fetchSection("upcoming");
              void fetchSection("history");
            }}
          />
        ) : nothingAtAll ? (
          <RecordsState
            icon="calendar"
            title={RECORDS_COPY.emptyTitle}
            quiet
            body={
              <p>
                {RECORDS_COPY.emptyBody} <span lang="en">Ayla</span> {RECORDS_COPY.emptyBodyAyla}
              </p>
            }
            actions={
              <>
                <RecordButton variant="primary" onClick={findMaster}>
                  {RECORDS_COPY.findMaster}
                </RecordButton>
                <RecordButton variant="soft" icon="ayla" onClick={askAyla}>
                  Спросить <span lang="en">Ayla</span>
                </RecordButton>
              </>
            }
          />
        ) : (
          <>
            <div className="cr-segment" role="tablist" aria-label="Раздел записей">
              {(
                [
                  ["upcoming", RECORDS_COPY.tabUpcoming],
                  ["history", RECORDS_COPY.tabHistory],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  id={`records-tab-${key}`}
                  aria-selected={activeTab === key}
                  aria-controls="records-panel"
                  className={`cr-segment__item${activeTab === key ? " cr-segment__item--on" : ""}`}
                  onClick={() => setActiveTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div
              id="records-panel"
              role="tabpanel"
              aria-labelledby={`records-tab-${activeTab}`}
              className="cr-panel"
            >
              {activeTab === "upcoming" ? (
                <>
                  <section aria-labelledby="records-upcoming-heading">
                    <h2
                      id="records-upcoming-heading"
                      className={upcoming.kind === "error" ? "visually-hidden" : "cr-heading"}
                    >
                      {RECORDS_COPY.upcomingHeading}
                    </h2>
                    {upcoming.kind === "loading" && skeletons}
                    {upcoming.kind === "error" && (
                      <RecordsLoadError
                        err={upcoming.err}
                        online={online}
                        title={RECORDS_COPY.loadFailed}
                        onRetry={() => void fetchSection("upcoming")}
                      />
                    )}
                    {upcoming.kind === "ok" && upcomingItems.length === 0 && (
                      <div className="cr-note" role="status">
                        <p>{RECORDS_COPY.noUpcoming}</p>
                        <RecordButton variant="soft" onClick={findMaster}>
                          {RECORDS_COPY.findMaster}
                        </RecordButton>
                      </div>
                    )}
                    {upcomingItems.length > 0 && (
                      <ul className="cr-list" role="list">
                        {upcomingItems.map((b) => (
                          <li key={b.bookingId}>
                            <RecordCard item={b} section="upcoming" onOpen={() => open(b)} />
                          </li>
                        ))}
                      </ul>
                    )}
                    {loadMoreButton("upcoming", upcoming)}
                  </section>

                  {/* Кадр 1: прошедшие — под предстоящими. Свои загрузку и
                      сбой этот блок не показывает: у них есть вкладка. */}
                  {historyItems.length > 0 && (
                    <section aria-labelledby="records-history-heading">
                      <h2 id="records-history-heading" className="cr-heading">
                        {RECORDS_COPY.historyHeading}
                      </h2>
                      <ul className="cr-list" role="list">
                        {historyItems.slice(0, HISTORY_PREVIEW).map((b) => (
                          <li key={b.bookingId}>
                            <RecordCard item={b} section="history" onOpen={() => open(b)} />
                          </li>
                        ))}
                      </ul>
                      {(historyItems.length > HISTORY_PREVIEW ||
                        (history.kind === "ok" && history.data.nextCursor)) && (
                        <div className="cr-more">
                          <RecordButton variant="text" onClick={() => setActiveTab("history")}>
                            {RECORDS_COPY.allHistory}
                          </RecordButton>
                        </div>
                      )}
                    </section>
                  )}
                </>
              ) : (
                <>
                  {filterEnabled && (
                    <div
                      className="records-screen__filter-row"
                      role="group"
                      aria-label="Фильтр прошедших записей"
                    >
                      {[FILTER_ALL, ...masterOptions].map((name) => (
                        <button
                          key={name}
                          type="button"
                          className={`records-screen__chip${filter === name ? " records-screen__chip--active" : ""}`}
                          aria-pressed={filter === name}
                          onClick={() => setFilter(name)}
                        >
                          {name === FILTER_ALL ? "Все" : name}
                        </button>
                      ))}
                    </div>
                  )}
                  {history.kind === "loading" && skeletons}
                  {history.kind === "error" && (
                    <RecordsLoadError
                      err={history.err}
                      online={online}
                      title={RECORDS_COPY.loadFailed}
                      onRetry={() => void fetchSection("history")}
                    />
                  )}
                  {history.kind === "ok" && historyItems.length === 0 && (
                    <div className="cr-note" role="status">
                      <p>{RECORDS_COPY.noHistory}</p>
                    </div>
                  )}
                  {historyItems.length > 0 && shownHistory.length === 0 && (
                    <div className="cr-note" role="status">
                      <p>{RECORDS_COPY.filterEmpty}</p>
                      <RecordButton variant="soft" onClick={() => setFilter(FILTER_ALL)}>
                        {RECORDS_COPY.filterReset}
                      </RecordButton>
                    </div>
                  )}
                  {shownHistory.length > 0 && (
                    <ul className="cr-list" role="list">
                      {shownHistory.map((b, index) =>
                        index === 0 ? (
                          // Кадр 12: у самой свежей — действия, блоком под карточкой.
                          <li key={b.bookingId} className="cr-card-group">
                            <RecordCard item={b} section="history" onOpen={() => open(b)} />
                            <RecordButton
                              variant="soft"
                              icon="repeat"
                              onClick={repeat}
                              disabled={!online}
                              title={online ? undefined : OFFLINE_HINT}
                            >
                              {RECORDS_COPY.repeat}
                            </RecordButton>
                            <RecordButton variant="soft" icon="details" onClick={() => open(b)}>
                              {RECORDS_COPY.details}
                            </RecordButton>
                            {b.canRate && (
                              <RecordButton
                                variant="soft"
                                icon="star"
                                onClick={() => rate(b)}
                                disabled={!online}
                                title={online ? undefined : OFFLINE_HINT}
                              >
                                {RECORDS_COPY.rate}
                              </RecordButton>
                            )}
                          </li>
                        ) : (
                          <li key={b.bookingId}>
                            <RecordCard item={b} section="history" onOpen={() => open(b)} />
                          </li>
                        ),
                      )}
                    </ul>
                  )}
                  {loadMoreButton("history", history)}
                </>
              )}
            </div>
          </>
        )}
      </main>

      {/* Панель — вне веток состояний: выход есть при любом исходе загрузки. */}
      <CustomerTabBar active="records" />
    </div>
  );
}
