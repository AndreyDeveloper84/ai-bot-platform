/**
 * «Сегодня» — главный экран пилотной салонной админки (DRF-1235).
 *
 * Адрес: `/admin/today`. Каркас, не содержимое.
 *
 * # Что этот экран делает и чего не делает
 *
 * Журнал дня — «Сейчас», «Дальше», лента изменений Ayla, «Требует
 * внимания», «Мастера сегодня» — это DRF-1236, и здесь его нет.
 *
 * Но это и не заглушка «скоро будет». Ручка дня существует и работает
 * (`GET /api/v1/admin/day/`, `apps/admin_api/urls.py::salon_day`),
 * поэтому экран за ней ходит и честно показывает то, что она вернула:
 * дату салона и сколько записей на этот день. Пусто — так и сказано;
 * не ответила — `StateError` с повтором, тот же, которым пользуются
 * семь админ-экранов.
 *
 * # Дата приходит с сервера
 *
 * `response.date` и `response.timezone` — часовой пояс салона, а не
 * устройства. Посчитать «сегодня» на телефоне администратора, который
 * летит в другом часовом поясе, значило бы показать чужой день. По той
 * же причине заголовок даты не рисуется, пока ответа нет.
 *
 * # Чего здесь нет намеренно
 *
 * * **Кнопки «Новая запись».** Она есть на макете и ведёт в живой
 *   `/admin/booking/new`. Это содержимое главного экрана — DRF-1236, и
 *   размещать её раньше, чем спроектирован сам экран, значит решать за
 *   ту задачу.
 * * **Телефона клиента.** Его нет в полезной нагрузке вовсе (DRF-1039),
 *   и добавлять его нечем.
 * * **Поля «Кабинет».** Макет обещает его в каждой строке записи, в
 *   `DayVisit` (`apps/admin_api/services/salon_day.py`) такого поля нет.
 *   См. отчёт по задаче: расхождение макета с данными вынесено
 *   владельцу, а не решено здесь.
 */

import { useCallback, useEffect, useState } from "react";

import { StateError } from "../../components/StateError";
import {
  getSalonDay,
  type MeResponse,
  type SalonDayResponse,
} from "../../lib/admin-api";
import { SalonPilotFrame } from "./SalonPilotFrame";

/** `2026-09-07` → `Понедельник, 7 сентября` — как в шапке макета. */
function formatDayLine(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  // Полдень, а не полночь: на переходе летнего времени полночь ± сдвиг
  // попадает на соседние сутки, и подпись разъезжается с датой.
  const dt = new Date(y, m - 1, d, 12);
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    weekday: "long",
  }).format(dt);
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

export function SalonPilotTodayScreen({ me }: { me: MeResponse }) {
  const [day, setDay] = useState<SalonDayResponse | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    setLoading(true);
    setErr(null);
    getSalonDay(undefined, { signal: ctrl.signal })
      .then((data) => {
        if (!alive) return;
        setDay(data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!alive || ctrl.signal.aborted) return;
        setErr(e);
        setLoading(false);
      });
    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const subtitle = day ? formatDayLine(day.date) : null;

  return (
    <SalonPilotFrame me={me} title="Сегодня" subtitle={subtitle}>
      {loading ? (
        <p className="salon-pilot__note" role="status">
          Загружаем день салона…
        </p>
      ) : null}
      {!loading && err ? <StateError err={err} onRetry={retry} /> : null}
      {!loading && !err && day ? (
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>
            {day.summary.total === 0
              ? "Записей на сегодня нет."
              : `Записей на сегодня: ${day.summary.total}.`}
          </p>
        </div>
      ) : null}
    </SalonPilotFrame>
  );
}
