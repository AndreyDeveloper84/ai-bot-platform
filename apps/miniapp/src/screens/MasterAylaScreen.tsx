/**
 * Раздел «Ayla» — диалог мастера с ассистентом.
 *
 * Адрес: `/master/ayla`
 *
 * Решение владельца, OD-7 от 21.08.2026 (повторено 05.09 в DRF-1180),
 * дословно:
 *
 *     «Раздел «Ayla» — это диалог мастера с Ayla, тот же, что в боте,
 *      но через Mini App. Не список клиентских переписок.»
 *
 * Отсюда две вещи, которые легко сделать иначе и получить другой экран:
 *
 * 1. История приходит с сервера
 *    (`GET /api/v1/master/assistant/history`), а не копится в
 *    состоянии вкладки. Нить одна на бот и приложение: спросил по
 *    дороге в MAX — дочитал в приложении.
 * 2. Собеседник один — Ayla. Ни списка клиентов, ни выбора адресата:
 *    мастер не переписывается с клиентом (DRF-1039), и эта поверхность
 *    такой возможности не открывает.
 *
 * # Действие, меняющее данные, не выполняется молча
 *
 * Эпик DRF-1180: «сначала она должна показать, что именно собирается
 * сделать, получить подтверждение пользователя и только после этого
 * выполнять действие». Ответ сервера может нести `pending_action` —
 * это ПРЕДЛОЖЕНИЕ. Экран рисует карточку со сводкой и двумя кнопками;
 * пока «Подтвердить» не нажата, `POST /assistant/confirm` не
 * вызывается вовсе. Аргументы лежат внутри подписанного талона, не в
 * состоянии экрана: показать одно, а отправить другое здесь нечем.
 *
 * # Нижняя навигация не тронута
 *
 * `MasterTabBar` сегодня четырёхразделная. Перевод 4 → 3
 * (`Сегодня | Расписание | Ayla`) идёт вместе с удалением экранов
 * переписки — это DRF-1255, и он упирается в неотвеченные вопросы
 * владельца. Пока экран живёт на своём адресе, а вход в него — карточка
 * на «Сегодня». Убрать вкладку раньше, чем появится экран, значило бы
 * увести человека в пустоту.
 *
 * # Каркас диалога — общий
 *
 * DRF-2119: лента, ввод и карточка предложения вынесены в `AylaChat` и
 * делятся с админским разделом «Ayla» (`/admin/ayla`). Этот экран
 * задаёт только, чей это ассистент (мастерская тройка API).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AylaChat, type AylaChatApi } from "../components/AylaChat";
import { MasterAvatar } from "../components/MasterAvatar";
import { MasterTabBar } from "../components/MasterTabBar";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  askAyla,
  confirmAylaAction,
  getAylaContext,
  getAylaHistory,
  type AylaContextResponse,
} from "../lib/master-api";
import { backTo } from "../lib/screen-back";

const GREETING =
  "Спросите про день, загрузку или свободные окна — отвечу по вашему расписанию.";

/** Стартовый экран Ayla — тексты макета DRF-1187 дословно (DRF-2153, М-5). */
const START_COPY = {
  visits: (n: number) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    const word =
      mod10 === 1 && mod100 !== 11
        ? "запись"
        : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)
          ? "записи"
          : "записей";
    return `Сегодня ${n} ${word}`;
  },
  noVisits: "Сегодня записей нет",
  next: (name: string, time: string) => `Следующая — ${name} в ${time}`,
  service: (name: string, min: number) => `${name} · ${min} мин`,
  whatToDo: "Что можно сделать",
  /** Подписи чипов по макету — если сервер их не прислал. */
  hints: {
    "Что у меня сегодня?": "Покажите расписание дня",
    "Когда я свободен завтра?": "Свободные окна на завтра",
    "Добавить запись": "Создать новую запись в расписании",
    "Изменить рабочий день": "Изменить график или недоступность",
  } as Record<string, string>,
} as const;

/** Карточка контекста дня + чипы (экран 1 макета). */
function AylaStartScreen({
  context,
  onChip,
}: {
  context: AylaContextResponse | null;
  onChip: (text: string) => void;
}) {
  if (context === null) return <p className="ayla-empty">{GREETING}</p>;
  const { today } = context;
  const chips =
    context.chips.length > 0 ? context.chips : Object.keys(START_COPY.hints);
  return (
    <div className="ayla-start">
      <section
        className="ayla-card ayla-start__context"
        aria-label="Контекст дня"
      >
        <div className="ayla-start__context-body">
          {today.count === 0 || today.next === null ? (
            <p className="ayla-card__title">
              {today.count === 0
                ? START_COPY.noVisits
                : START_COPY.visits(today.count)}
            </p>
          ) : (
            <>
              <p className="ayla-card__title">
                {START_COPY.visits(today.count)}
              </p>
              <p className="ayla-card__row">
                {START_COPY.next(
                  today.next.client_name_initial,
                  today.next.time,
                )}
              </p>
              <p className="ayla-card__row ayla-card__row--muted">
                {START_COPY.service(
                  today.next.service_name,
                  today.next.duration_min,
                )}
              </p>
            </>
          )}
        </div>
        <span className="ayla-start__icon" aria-hidden="true">
          📅
        </span>
      </section>
      <p className="ayla-start__label">{START_COPY.whatToDo}</p>
      <ul className="ayla-card__list ayla-start__chips">
        {chips.map((text) => (
          <li key={text}>
            <button
              type="button"
              className="ayla-card__option ayla-start__chip"
              onClick={() => onChip(text)}
            >
              <span className="ayla-card__row-main">{text}</span>
              <span className="ayla-card__row-meta">
                {context.chip_hints?.[text] ?? START_COPY.hints[text] ?? ""}
              </span>
              <span aria-hidden="true">›</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MasterAylaScreen() {
  useScreenBack(backTo("/master/dashboard"));
  const navigate = useNavigate();

  // Каркас диалога общий с администратором (DRF-2119, `AylaChat`); здесь
  // — только чей это ассистент: мастерская тройка history / ask / confirm.
  const api = useMemo<AylaChatApi>(
    () => ({
      history: () => getAylaHistory(),
      ask: (text, select) => askAyla(text, select),
      confirm: (token) => confirmAylaAction(token),
    }),
    [],
  );

  // DRF-2153: стартовый экран — контекст дня и чипы. Отказ контекста — не
  // отказ диалога: тогда прежнее приглашение.
  const [context, setContext] = useState<AylaContextResponse | null>(null);
  useEffect(() => {
    let alive = true;
    getAylaContext()
      .then((res) => {
        if (alive) setContext(res);
      })
      .catch(() => {
        /* приглашение по умолчанию */
      });
    return () => {
      alive = false;
    };
  }, []);

  const startScreen = useCallback(
    (send: (text: string) => void) => (
      <AylaStartScreen context={context} onChip={send} />
    ),
    [context],
  );

  return (
    <main className="screen ayla-screen">
      <header className="ayla-header">
        <div className="ayla-header__top">
          <h1 className="ayla-header__title">Ayla</h1>
          {/* DRF-2121 (§28 п.3): вход в профиль — аватар на каждом из трёх разделов. */}
          <MasterAvatar />
        </div>
        <p className="ayla-header__sub">Помощник по вашему расписанию</p>
      </header>

      <AylaChat
        api={api}
        greeting={GREETING}
        declineLabel="Отмена"
        startScreen={startScreen}
        onOpen={(url) => navigate(url)}
      />

      {/* Значки вкладок этот экран не считает: они приходят с
          дашборда (`tab_badges`), и запрашивать дашборд ради трёх
          чисел на экране диалога — лишний круг к серверу на каждом
          открытии. Панель здесь нужна как навигация, не как сводка. */}
      <MasterTabBar scheduleHasPendingChange={false} />
    </main>
  );
}
