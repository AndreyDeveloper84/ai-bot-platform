/**
 * F3 — Date + time picker with smart suggestions + state-dependent
 * wording + master substitution.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §5 (§5.1 layout / §5.2
 * suggestions rationale / §5.3 master substitution Q-BF-3 / §5.4
 * states).
 *
 * Voice rules (founder cut #4 — state-dependent suggestions header):
 *   - Anonymous / new customer: «Ближайшие свободные»
 *   - Registered WITH BEHAVIOR: «Похоже подойдёт»
 *   - Loyal (5+ visits): «Твоё обычное время»
 *
 * «WITH BEHAVIOR» — не оборот речи, а условие. Персонализирующие
 * заголовки разрешены только когда бэкенд действительно пометил слоты
 * (`is_suggested`). Без пометки в блоке лежат ПЕРВЫЕ ДВА СЛОТА ПО
 * ВРЕМЕНИ, и честное имя им — «Ближайшие свободные»; так и записано в
 * рационале спеки §5.1: «не имитировать персонализацию там где её нет».
 *
 * Master substitution (Q-BF-3) — verbatim founder copy:
 *   «Анна занята на 2 недели вперёд. Если хочешь раньше, есть Карина —
 *    тоже делает маникюр гель-лак.»
 *   [Посмотреть Карину] [Дождаться Анну]
 *
 * Anti-pattern enforcement: NEVER «Карина лучше / Рекомендуем» —
 * frontend ONLY shows the founder-locked copy template above.
 *
 * WCAG 2.2 AA (Tau §11):
 *   - Slots grouped by day in `<ul>` per spec §11.3
 *   - Slot = `<button>`
 *   - ✨ suggestion marker accompanied by text «обычное время» (not
 *     color-only) — handled via aria-label.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import {
  DelayedSkeleton,
  Skeleton,
  SlotGridSkeleton,
} from "../components/Skeleton";
import { OfflineBanner } from "../components/OfflineBanner";
import { StateError } from "../components/StateError";
import { useOnline } from "../hooks/useOnline";
import { useHaptics } from "../hooks/useHaptics";
import { getCustomerSlots } from "../lib/customer-booking";
import { formatDateLabel, formatSlotTime } from "../lib/format";
import {
  SUGGESTED_MARK,
  SUGGESTED_NOTE,
  daysWithCounts,
  groupByDayPart,
  type DayWithCount,
} from "../lib/booking-time";
import { setVisitAt, useBookingDraft } from "../state/booking";
import { backTo } from "../lib/screen-back";

interface SlotRow {
  date: string;
  start: string;
  isSuggested?: boolean;
}

type State =
  | { kind: "loading" }
  | {
      kind: "ok";
      // Server slots, ordered chronologically.
      slots: SlotRow[];
    }
  | { kind: "error"; err: unknown };

/*
 * Словарь заголовков блока подсказок (founder cut #4) снят вместе с самим
 * блоком — DRF-2178, кадр 3 макета DRF-1320 его не знает.
 *
 * Что именно снято, чтобы это не пришлось откапывать: три состояния —
 * «Ближайшие свободные» (нет сигнала персонализации либо нет initData),
 * «Похоже подойдёт» (зарегистрирован), «Твоё обычное время» (лояльный,
 * 5+ визитов), — и определение режима по `channelIdentity()`.
 *
 * Вместо них макет даёт ОДНУ пометку у времени
 * (`booking-time.ts::SUGGESTED_NOTE`), и она по-прежнему показывается
 * только по серверному признаку: обещание персонализации без сигнала
 * не вернулось ни в каком виде — это и было сутью DRF-1319.
 *
 * Конфликт двух текстов владельца (макет против founder cut) вынесен ему
 * вопросом; если он выберет три заголовка, они возвращаются сюда, и
 * состояние по-прежнему решается ОДНИМ местом.
 */

export function CustomerSlotsScreen() {
  const online = useOnline();
  const navigate = useNavigate();
  const { masterId } = useParams<{ masterId: string }>();
  const haptics = useHaptics();
  const draft = useBookingDraft();
  const [state, setState] = useState<State>({ kind: "loading" });
  // DRF-2178 — окно, которое СПРОСИЛИ: полоса дней строится по нему, а
  // не по ответу (сервер шлёт только свободное).
  const [window, setWindow] = useState<{ from: string; to: string } | null>(null);
  // Выбранный день полосы; `null` — ещё не выбран, возьмём первый с окнами.
  const [pickedDay, setPickedDay] = useState<string | null>(null);
  // DRF-1776 — «Другие даты»: сдвиг окна в днях; 0 — ближайшие две недели.
  const [windowOffset, setWindowOffset] = useState(0);
  // DRF-1776 — слот, который только что оказался занят (возврат с
  // подтверждения по 409): назвать его, а не показать тот же список молча.
  const location = useLocation();
  const unavailableSlot =
    (location.state as { unavailableSlot?: string } | null)?.unavailableSlot ?? null;

  // Возврат (DRF-1493) — к карточке того мастера, чьи окна показаны.
  // Не `-1`: по deep link в оформление истории нет, а мастер известен
  // из адреса. Без `masterId` (адрес испорчен) — в каталог.
  const back = backTo(
    masterId ? `/customer/masters/${masterId}` : "/customer/catalog",
  );

  const load = useCallback(() => {
    if (!masterId || !draft.serviceId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    getCustomerSlots({
      masterId,
      serviceId: draft.serviceId,
      days: 14,
      offsetDays: windowOffset,
    })
      .then(({ slots, dateFrom, dateTo }) => {
        if (cancelled) return;
        setWindow({ from: dateFrom, to: dateTo });
        // Re-shape to our local row type; `is_suggested` is a future
        // backend field — absent today, defaults to false.
        // `is_suggested` — серверная пометка (Tau §5.2). Ручка её
        // сегодня не шлёт; читаем то, что пришло, и НЕ подставляем
        // ничего от себя: отсутствие пометки = персонализации нет.
        const rows = slots.map((s) => ({
          date: s.date,
          start: s.start,
          isSuggested:
            (s as { is_suggested?: boolean }).is_suggested === true,
        }));
        setState({ kind: "ok", slots: rows });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [masterId, draft.serviceId, windowOffset]);

  useEffect(() => {
    if (!masterId || !draft.serviceId) {
      // Missing prerequisites — bounce back to catalog rather than
      // showing an empty / broken screen.
      navigate("/customer/catalog", { replace: true });
      return;
    }
    return load();
  }, [masterId, draft.serviceId, navigate, load]);

  // Сдвинули окно — прежний выбранный день в нём может не лежать.
  useEffect(() => {
    setPickedDay(null);
  }, [windowOffset]);

  const slotsByDate = useMemo(() => {
    const m = new Map<string, SlotRow[]>();
    if (state.kind !== "ok") return m;
    for (const s of state.slots) {
      const arr = m.get(s.date) ?? [];
      arr.push(s);
      m.set(s.date, arr);
    }
    return m;
  }, [state]);

  // DRF-2178 — полоса дней и части суток кадра 3 макета DRF-1320.
  // Блок «Ближайшие свободные» снят: его содержимое — те же слоты
  // выбранного дня, и на кадре они лежат под Утро/День/Вечер. Недостижимым
  // не стало ничего, исчез дубль ярлыка (отступление названо в теле PR;
  // вопрос владельцу 45г — вернуть блок или оставить снятым).
  const days = useMemo<DayWithCount[]>(() => {
    if (state.kind !== "ok" || window === null) return [];
    return daysWithCounts(state.slots, window.from, window.to);
  }, [state, window]);

  const selectedDay = useMemo(() => {
    if (pickedDay !== null) return pickedDay;
    return days.find((d) => d.count > 0)?.date ?? null;
  }, [pickedDay, days]);

  const dayGroups = useMemo(() => {
    if (selectedDay === null) return [];
    return groupByDayPart(slotsByDate.get(selectedDay) ?? []);
  }, [selectedDay, slotsByDate]);

  function onPickSlot(slotIso: string) {
    haptics.selection();
    setVisitAt(slotIso);
  }

  function onContinue() {
    if (!draft.visitAt) return;
    navigate("/customer/booking/confirm");
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <DelayedSkeleton loading>
          <div className="date-strip">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} width="64px" height="60px" radius="var(--r-md)" />
            ))}
          </div>
          <SlotGridSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <StateError err={state.err} onRetry={load} screenId="customer-slots" />
      </ScreenLayout>
    );
  }

  // Tau §5.3 master substitution — full 14-day fully-booked case.
  // DRF-1776: из этого состояния есть два пути, оба локальные (не в C01):
  // «Другие даты» — следующее окно того же мастера; «Другой специалист» —
  // выбор мастера под ту же услугу.
  if (state.slots.length === 0) {
    return (
      <ScreenLayout back={back} title="Выбери время">
        <MasterSubstitutionCallout
          masterName={draft.masterName ?? "она"}
          laterWindow={windowOffset > 0}
          canLookFurther={windowOffset + WINDOW_DAYS < MAX_LOOKAHEAD_DAYS}
          onOtherDates={() => setWindowOffset((o) => o + WINDOW_DAYS)}
          onOtherMaster={() => navigate("/customer/book/master")}
        />
      </ScreenLayout>
    );
  }


  return (
    <ScreenLayout
      back={back}
      title="Выбери время"
      cta={
        <StickyCta onClick={onContinue} disabled={!draft.visitAt || !online}>
          {draft.visitAt ? "Дальше" : "Выбери слот"}
        </StickyCta>
      }
    >
      <OfflineBanner online={online} />
      {/* DRF-1776 — слот занят между выбором и подтверждением: сказать,
          какой именно, и оставить человека здесь же (не в C01). */}
      {unavailableSlot && (
        <div className="callout" role="status" data-testid="slot-unavailable-note">
          <p style={{ margin: 0 }}>
            {formatSlotTime(unavailableSlot)} уже заняли — выбери другое время.
          </p>
        </div>
      )}
      {windowOffset > 0 && (
        <p className="customer-slots__window-note" role="status">
          Окна на две недели позже обычного.{" "}
          <button type="button" className="goal-select__minor-action" onClick={() => setWindowOffset(0)}>
            Ближайшие
          </button>
        </p>
      )}
      {/* Кадр 3 макета DRF-1320 — полоса дней со счётчиком окон. День
          без окон остаётся в полосе и говорит «нет мест»: выброси его —
          и человек увидит не «сегодня уже поздно», а «сегодня не
          бывает». */}
      <section aria-label="Дни со свободным временем">
        {/* Видимого заголовка у полосы нет: на кадре макета его нет, а
            придумывать свой — значит добавить человеку текст, которого
            владелец не писал. Имя секции остаётся для скринридера. */}
        <ul className="customer-slots__day-strip" role="list">
          {days.map((day) => {
            const active = day.date === selectedDay;
            return (
              <li key={day.date}>
                <button
                  type="button"
                  className={`customer-slots__day-chip${active ? " customer-slots__day-chip--active" : ""}`}
                  aria-pressed={active}
                  disabled={day.count === 0}
                  aria-label={`${formatDateLabel(day.date)}, ${day.label}`}
                  onClick={() => setPickedDay(day.date)}
                >
                  <span className="customer-slots__day-chip-date">
                    {formatDateLabel(day.date)}
                  </span>
                  <span className="customer-slots__day-chip-count">{day.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Части суток выбранного дня. Пустая группа не рисуется — заголовок
          без окон под ним обещал бы время, которого нет. */}
      {dayGroups.map((group) => (
        <section key={group.key} aria-labelledby={`slots-part-${group.key}`}>
          <h3 id={`slots-part-${group.key}`} className="customer-slots__day-title">
            {group.label}
          </h3>
          <ul className="customer-slots__day-list" role="list">
            {group.slots.map((slot) => {
              const active = draft.visitAt === slot.start;
              // ПРАВКА 2 макета: пометка — только по серверному признаку.
              // Сегодня он не приходит, и её не видно ни разу; выдумывать
              // предпочтение за человека мы не станем.
              const suggested = (slot as SlotRow).isSuggested === true;
              return (
                <li key={slot.start}>
                  <button
                    type="button"
                    className={`customer-slots__cell${active ? " customer-slots__cell--active" : ""}`}
                    aria-pressed={active}
                    aria-label={`${formatDateLabel(slot.date)} в ${formatSlotTime(slot.start)}${
                      suggested ? ", обычное время" : ""
                    }`}
                    onClick={() => onPickSlot(slot.start)}
                  >
                    {formatSlotTime(slot.start)}
                    {suggested ? <span aria-hidden="true"> {SUGGESTED_MARK}</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
          {group.slots.some((slot) => (slot as SlotRow).isSuggested === true) ? (
            <p className="customer-slots__suggested-note">
              <span aria-hidden="true">{SUGGESTED_MARK} </span>
              {SUGGESTED_NOTE}
            </p>
          ) : null}
        </section>
      ))}

    </ScreenLayout>
  );
}

/**
 * Master substitution callout (Q-BF-3 verbatim founder copy).
 *
 * **Critical**: copy is locked verbatim per Tau §5.3 + founder cut.
 * Anti-pattern: NEVER «Карина лучше / Рекомендуем». Frontend renders
 * this template; substitute candidates will be plumbed by backend.
 *
 * NOTE: We don't have a substitution candidate from the current
 * backend; this is a generic «нет слотов» state. When backend ships
 * `substitution_candidates`, swap the placeholder for real data and
 * keep the wording template.
 */
/** Размер одного окна слотов — потолок сервера на запрос. */
const WINDOW_DAYS = 14;
/** Докуда листать «Другие даты»: дальше расписание обычно ещё не открыто. */
const MAX_LOOKAHEAD_DAYS = 56;

export const OTHER_DATES_LABEL = "Другие даты";
export const OTHER_MASTER_LABEL = "Другой специалист";

function MasterSubstitutionCallout({
  masterName,
  laterWindow,
  canLookFurther,
  onOtherDates,
  onOtherMaster,
}: {
  masterName: string;
  laterWindow: boolean;
  canLookFurther: boolean;
  onOtherDates: () => void;
  onOtherMaster: () => void;
}) {
  return (
    <div className="callout" role="status">
      <p style={{ margin: 0 }}>
        {laterWindow
          ? `${masterName} занята и на следующие 2 недели.`
          : `${masterName} занята на 2 недели вперёд.`}
      </p>
      <p style={{ margin: "var(--s-2) 0 0 0", color: "var(--c-text-secondary)" }}>
        {canLookFurther
          ? "Можно посмотреть даты позже или выбрать другого специалиста на ту же услугу."
          : "Дальше расписание ещё не открыто — можно выбрать другого специалиста на ту же услугу."}
      </p>
      <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)", flexWrap: "wrap" }}>
        {canLookFurther && (
          <button type="button" className="btn-secondary" onClick={onOtherDates}>
            {OTHER_DATES_LABEL}
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={onOtherMaster}>
          {OTHER_MASTER_LABEL}
        </button>
      </div>
    </div>
  );
}
