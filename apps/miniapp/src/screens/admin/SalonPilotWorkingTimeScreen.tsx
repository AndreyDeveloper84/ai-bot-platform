/**
 * «Рабочее время» — список мастеров (DRF-2936, макет DRF-2865, кадр 1).
 *
 * Адрес `/admin/working-time`, вход — «⋯» в «Расписании» (DRF-1235 утвердил
 * в этом меню ровно один пункт). Нижняя панель — та же тройка пилота, и
 * отмечена на ней «Расписание»: рабочее время — его раздел (freeze DRF-2865).
 *
 * Корнем экран не объявляется: человек пришёл из «Расписания», и
 * системная «назад» ведёт туда.
 *
 * Мастер, с которым пришли (`state.masterId`), отмечен «мишенью» вместо
 * шеврона — как выбранный на макете.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { SystemState } from "../../components/OperationalSystemState";
import { SalonPilotTabBar } from "../../components/SalonPilotTabBar";
import { useOnline } from "../../hooks/useOnline";
import { useScreenBack } from "../../hooks/useScreenBack";
import { listMasters, type MasterListItem } from "../../lib/admin-api";
import { signalReady } from "../../lib/max-sdk";
import { WT_COPY, workingTimeWeekPath } from "../../lib/salon-working-time";
import { backTo } from "../../lib/screen-back";
import {
  IconChevron,
  IconPeople,
  IconSearch,
  IconTarget,
  WtAvatar,
  WtHeader,
  WtLoadError,
  WtStateCard,
} from "./WorkingTimeParts";

/** Сколько страниц читать за раз: салон пилота — десятки мастеров, не тысячи. */
const MAX_PAGES = 5;
const SEARCH_DEBOUNCE_MS = 300;

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; items: MasterListItem[]; query: string }
  | { kind: "error"; err: unknown };

function stateMasterId(state: unknown): string | null {
  const raw = (state as { masterId?: unknown } | null | undefined)?.masterId;
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

async function readAll(search: string, signal: AbortSignal): Promise<MasterListItem[]> {
  const items: MasterListItem[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const res = await listMasters(
      { is_active: true, search: search || undefined, cursor, limit: 100 },
      { signal },
    );
    items.push(...res.items);
    if (!res.next_cursor) break;
    cursor = res.next_cursor;
  }
  return items;
}

export function SalonPilotWorkingTimeScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const online = useOnline();
  const selectedId = stateMasterId(location.state);
  useScreenBack(backTo("/admin/schedule"));

  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const ready = useRef(false);

  const load = useCallback(async (search: string, signal: AbortSignal) => {
    try {
      const items = await readAll(search.trim(), signal);
      if (signal.aborted) return;
      setPhase({ kind: "ready", items, query: search.trim() });
    } catch (err) {
      if (signal.aborted) return;
      setPhase({ kind: "error", err });
    } finally {
      if (!ready.current && !signal.aborted) {
        ready.current = true;
        signalReady();
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => void load(query, controller.signal),
      query ? SEARCH_DEBOUNCE_MS : 0,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, load]);

  // Кадр 10: «Нет мастеров» и ошибка загрузки — карточка одна, без поиска:
  // искать не в чем. Поиск, давший пустоту, остаётся — по нему уходят назад.
  const emptySalon = phase.kind === "ready" && phase.items.length === 0 && phase.query === "";
  const showSearch = !(phase.kind === "error" || emptySalon) || query !== "";

  const retry = () => {
    setPhase({ kind: "loading" });
    const controller = new AbortController();
    void load(query, controller.signal);
  };

  return (
    <div className="screen wt-screen">
      <WtHeader title={WT_COPY.title} />
      <div className="wt-body">
        {!online ? <SystemState kind="offline" /> : null}
        {showSearch ? (
          <label className="wt-search">
            <span className="wt-search__icon" aria-hidden="true">
              <IconSearch />
            </span>
            <input
              type="search"
              className="wt-search__input"
              placeholder={WT_COPY.search}
              aria-label={WT_COPY.search}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        ) : null}

        {phase.kind === "loading" ? <SystemState kind="loading" /> : null}
        {phase.kind === "error" ? <WtLoadError page err={phase.err} onRetry={retry} /> : null}
        {emptySalon ? (
          <WtStateCard
            page
            tone="neutral"
            icon={<IconPeople />}
            title={WT_COPY.empty.noMasters.title}
            body={WT_COPY.empty.noMasters.body}
          >
            <button
              type="button"
              className="btn-secondary wt-btn"
              onClick={() => navigate("/admin/team/add")}
            >
              {WT_COPY.empty.noMasters.cta}
            </button>
          </WtStateCard>
        ) : null}
        {phase.kind === "ready" && phase.items.length === 0 && phase.query !== "" ? (
          <SystemState kind="empty" text={WT_COPY.empty.noMatches} />
        ) : null}
        {phase.kind === "ready" && phase.items.length > 0 ? (
          <ul className="wt-masters" aria-label="Мастера">
            {phase.items.map((m) => {
              const selected = m.id === selectedId;
              return (
                <li key={m.id}>
                  <button
                    type="button"
                    className={`wt-master${selected ? " wt-master--selected" : ""}`}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => navigate(workingTimeWeekPath(m.id))}
                  >
                    <WtAvatar name={m.name} photoUrl={m.photo_url} />
                    <span className="wt-master__text">
                      <span className="wt-master__name">{m.name}</span>
                      {m.specialization ? (
                        <span className="wt-master__spec">{m.specialization}</span>
                      ) : null}
                    </span>
                    <span className="wt-master__mark" aria-hidden="true">
                      {selected ? <IconTarget /> : <IconChevron />}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
      <SalonPilotTabBar active="schedule" />
    </div>
  );
}
