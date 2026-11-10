/**
 * Загрузка «Расписания» салона v2 (DRF-2933): день салона и день мастера.
 *
 * Два хука на два экрана — `/admin/schedule` и кадр 12 — чтобы оба читали
 * день одним и тем же порядком и одинаково понимали «не обновилось».
 *
 * # Порядок чтения дня
 *
 * Записи — из `admin/day/` (зеркало), смены и закрытия — из
 * `admin/day/frame/` (каталог). «Сегодня» у этих ручек разное: день салона
 * считает его по поясу салона, каталог — по поясу мастера. Поэтому кадр
 * всегда запрашивается с ЯВНОЙ датой: выбранной человеком, а если её нет —
 * той, что вернул день салона. Кадр, ответивший про другой день,
 * отбрасывается: смены вторника под записями среды хуже, чем «смены не
 * загрузились».
 *
 * # Сбой обновления не стирает показанное
 *
 * Заморозка DRF-2854, MUST FIX 7: расписание остаётся видимым как последнее
 * известное. Перечитывание того же дня при отказе оставляет прежние данные
 * и поднимает `refreshErr`; сбой первого чтения и смена даты — экран ошибки,
 * а не чужой день под новой датой.
 */
import { useEffect, useRef, useState } from "react";

import {
  getMasterDaySchedule,
  getMasterSchedule,
  getSalonDay,
  getSalonDayFrame,
  type MasterDay,
  type MasterScheduleConfirmation,
  type SalonDayFrame,
  type SalonDayResponse,
} from "../lib/admin-api";
import { ApiError } from "../lib/api";

export interface SalonDayData {
  day: SalonDayResponse;
  /** Смены и закрытия; `null` — кадр не прочитан или ответил про другой день. */
  frame: SalonDayFrame | null;
  frameFailed: boolean;
  /**
   * Салон ведёт расписание в локальных таблицах (`BOOKING_VIA_AYLA_REST`
   * выключен): кадр каталога к нему не относится и отвечает названным
   * отказом. Это не «закрытия неизвестны» — закрытий каталога у него нет.
   */
  localSchedule: boolean;
  /** Когда пришёл ответ — пометка актуальности и ключ перечитывания зависимого. */
  loadedAt: number;
}

export type SalonDayPhase =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "ready"; data: SalonDayData; refreshing: boolean; refreshErr: unknown | null };

type FrameResult = { frame: SalonDayFrame | null; failed: boolean; local: boolean };

const LOCAL_SCHEDULE_SLUG = "frame_source_local_unsupported";

function readFrame(date: string, signal: AbortSignal): Promise<FrameResult> {
  return getSalonDayFrame(date, { signal }).then(
    (frame): FrameResult =>
      frame.date && frame.date !== date
        ? { frame: null, failed: true, local: false }
        : { frame, failed: false, local: false },
    (err: unknown): FrameResult => ({
      frame: null,
      failed: true,
      local: err instanceof ApiError && err.slug === LOCAL_SCHEDULE_SLUG,
    }),
  );
}

/**
 * День салона на дату. `wanted === null` — «сегодня салона»: дату называет
 * сервер, устройство её не считает.
 */
export function useSalonDay(wanted: string | null, attempt: number): SalonDayPhase {
  const [phase, setPhase] = useState<SalonDayPhase>({ kind: "loading" });
  /** Для какой запрошенной даты показаны данные; `undefined` — ещё ни для какой. */
  const shownFor = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const sameDay = shownFor.current === wanted;
    setPhase((prev) => (sameDay && prev.kind === "ready" ? { ...prev, refreshing: true } : { kind: "loading" }));

    void (async () => {
      // Дата известна — кадр идёт параллельно с днём; нет — после него, с его датой.
      const early = wanted ? readFrame(wanted, signal) : null;
      try {
        const day = await getSalonDay(wanted ?? undefined, { signal });
        const { frame, failed, local } = await (early ?? readFrame(day.date, signal));
        if (signal.aborted) return;
        const usable = frame && (!frame.date || frame.date === day.date) ? frame : null;
        shownFor.current = wanted;
        setPhase({
          kind: "ready",
          data: {
            day,
            frame: usable,
            frameFailed: failed || (frame !== null && usable === null),
            localSchedule: local,
            loadedAt: Date.now(),
          },
          refreshing: false,
          refreshErr: null,
        });
      } catch (err) {
        if (signal.aborted) return;
        setPhase((prev) =>
          sameDay && prev.kind === "ready" ? { ...prev, refreshing: false, refreshErr: err } : { kind: "error", err },
        );
      }
    })();

    return () => controller.abort();
  }, [wanted, attempt]);

  return phase;
}

export type MasterDayPhase =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | {
      kind: "ready";
      /** `null` — сервер не вернул этот день. */
      day: MasterDay | null;
      /** Когда пришли свободные окна: устаревшее окно — не действие. */
      fetchedAt: number;
      confirmation: MasterScheduleConfirmation | null;
      /** Подтверждение не прочитано — третье состояние, не «не подтверждено» (§29.5). */
      confirmationUnread: boolean;
    };

/**
 * День одного мастера: часы, блоки, свободные окна и конфликты из
 * `day-schedule` и состояние подтверждения графика. Дата обязательна и
 * уходит обоими параметрами: без `from` сервер считает «сегодня» по своему
 * поясу, а не по поясу салона.
 *
 * `stamp` — ключ перечитывания: день салона перечитали — перечитываем и
 * окна, иначе записи и окна оказались бы из разных моментов.
 */
export function useMasterDay(masterId: string | null, date: string | null, stamp: number): MasterDayPhase {
  const [phase, setPhase] = useState<MasterDayPhase>({ kind: "idle" });

  useEffect(() => {
    if (!masterId || !date) {
      setPhase({ kind: "idle" });
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    setPhase({ kind: "loading" });

    void (async () => {
      const confirmation = getMasterSchedule(masterId, { signal }).then(
        (schedule) => ({ value: schedule.confirmation, unread: false }),
        () => ({ value: null, unread: true }),
      );
      try {
        const res = await getMasterDaySchedule(masterId, { from: date, to: date }, { signal });
        const read = await confirmation;
        if (signal.aborted) return;
        setPhase({
          kind: "ready",
          day: res.days.find((d) => d.date === date) ?? null,
          fetchedAt: Date.now(),
          confirmation: read.value,
          confirmationUnread: read.unread,
        });
      } catch (err) {
        if (signal.aborted) return;
        setPhase({ kind: "error", err });
      }
    })();

    return () => controller.abort();
  }, [masterId, date, stamp]);

  return phase;
}
