/**
 * Карточки на «Сегодня» (DRF-2115, §50 п.6; DRF-2117): «Диалоги — N ждут
 * ответа», «График — N заявок», «Готовность — N проблем».
 *
 * * «Диалоги» — очередь handoff (`GET admin/handoff-queue/`): сколько
 *   клиентов ждут человека. Решение владельца 19.09: карточка на
 *   «Сегодня». Ведёт на экран очереди (`/admin/handoff`).
 * * «График» — заявки мастеров на смену графика, ожидающие решения
 *   (`GET admin/availability-requests/?status=pending`). Ведёт на экран
 *   заявок, который есть с M3-admin.
 * * «Готовность» — салонная готовность поимённо (`GET admin/readiness/`,
 *   #1878). Состояние приходит от экрана (`useSalonReadiness`): тот же
 *   ответ питает строку сводки, второй запрос не нужен. Ресепшну не
 *   рисуется (`hidden`): ручка ей 403, а карточка «не удалось проверить»
 *   на её экране была бы ложью о сбое. Ведёт на список (`/admin/readiness`).
 *
 * Число — только из ответа. Сбой ручки — карточка есть, числа нет и оно
 * НЕ ноль: «не удалось посчитать» — не «никто не ждёт». Список заявок
 * постраничный: считается первая страница, при хвосте — «N+».
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { ReadinessView } from "../../hooks/useSalonReadiness";
import { getAvailabilityRequests, getHandoffQueue } from "../../lib/admin-api";
import { READINESS_PATH, readinessCardText } from "../../lib/salon-readiness";

export const HANDOFF_QUEUE_PATH = "/admin/handoff";
export const AVAILABILITY_REQUESTS_PATH = "/admin/availability-requests";

type Count = { kind: "loading" } | { kind: "ok"; n: number; more: boolean } | { kind: "failed" };

/** «2 заявки», «5 заявок», «1 заявка». */
export function requestsLabel(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  let word = "заявок";
  if (mod10 === 1 && mod100 !== 11) word = "заявка";
  else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) word = "заявки";
  return `${n} ${word}`;
}

/** «1 ждёт ответа», «2 ждут ответа». */
export function waitingLabel(n: number): string {
  const one = n % 10 === 1 && n % 100 !== 11;
  return `${n} ${one ? "ждёт" : "ждут"} ответа`;
}

export function dialogsCardText(c: Count): string {
  if (c.kind === "loading") return "Диалоги — считаем…";
  if (c.kind === "failed") return "Диалоги — не удалось посчитать";
  if (c.n === 0) return "Диалоги — никто не ждёт";
  return `Диалоги — ${waitingLabel(c.n)}`;
}

export function scheduleCardText(c: Count): string {
  if (c.kind === "loading") return "График — считаем…";
  if (c.kind === "failed") return "График — не удалось посчитать";
  if (c.n === 0) return "График — заявок нет";
  return `График — ${requestsLabel(c.n)}${c.more ? "+" : ""}`;
}

const PENDING_PAGE = 50;

export function SalonTodayCards({ readiness }: { readiness: ReadinessView }) {
  const [dialogs, setDialogs] = useState<Count>({ kind: "loading" });
  const [schedule, setSchedule] = useState<Count>({ kind: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    getHandoffQueue({ signal: ctrl.signal })
      .then((res) => setDialogs({ kind: "ok", n: res.waiting, more: false }))
      .catch(() => {
        if (!ctrl.signal.aborted) setDialogs({ kind: "failed" });
      });
    getAvailabilityRequests({ status: "pending", limit: PENDING_PAGE }, { signal: ctrl.signal })
      .then((res) =>
        setSchedule({ kind: "ok", n: res.items.length, more: res.next_cursor !== null }),
      )
      .catch(() => {
        if (!ctrl.signal.aborted) setSchedule({ kind: "failed" });
      });
    return () => ctrl.abort();
  }, []);

  return (
    <div className="salon-today__cards">
      <Link className="salon-today__card" to={HANDOFF_QUEUE_PATH}>
        {dialogsCardText(dialogs)}
      </Link>
      <Link className="salon-today__card" to={AVAILABILITY_REQUESTS_PATH}>
        {scheduleCardText(schedule)}
      </Link>
      {readiness.kind !== "hidden" ? (
        <Link className="salon-today__card" to={READINESS_PATH}>
          {readinessCardText(readiness)}
        </Link>
      ) : null}
    </div>
  );
}
