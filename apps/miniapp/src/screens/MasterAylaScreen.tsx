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

import { useMemo } from "react";

import { AylaChat, type AylaChatApi } from "../components/AylaChat";
import { MasterAvatar } from "../components/MasterAvatar";
import { MasterTabBar } from "../components/MasterTabBar";
import { useScreenBack } from "../hooks/useScreenBack";
import { askAyla, confirmAylaAction, getAylaHistory } from "../lib/master-api";
import { backTo } from "../lib/screen-back";

const GREETING =
  "Спросите про день, загрузку или свободные окна — отвечу по вашему расписанию.";

export function MasterAylaScreen() {
  useScreenBack(backTo("/master/dashboard"));

  // Каркас диалога общий с администратором (DRF-2119, `AylaChat`); здесь
  // — только чей это ассистент: мастерская тройка history / ask / confirm.
  const api = useMemo<AylaChatApi>(
    () => ({
      history: () => getAylaHistory(),
      ask: (text) => askAyla(text),
      confirm: (token) => confirmAylaAction(token),
    }),
    [],
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

      <AylaChat api={api} greeting={GREETING} />

      {/* Значки вкладок этот экран не считает: они приходят с
          дашборда (`tab_badges`), и запрашивать дашборд ради трёх
          чисел на экране диалога — лишний круг к серверу на каждом
          открытии. Панель здесь нужна как навигация, не как сводка. */}
      <MasterTabBar scheduleHasPendingChange={false} />
    </main>
  );
}
