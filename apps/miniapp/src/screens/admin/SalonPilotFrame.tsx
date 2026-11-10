/**
 * Рамка экрана салонной админки (DRF-1235 → DRF-2115).
 *
 * На макете (DRF-1236) все три экрана начинаются одинаково: строка с
 * названием салона, под ней крупный заголовок раздела, под ним —
 * содержимое, внизу — постоянная панель из трёх вкладок. Рамка вынесена
 * сюда, чтобы три экрана не разъехались по отступам и по тому, откуда
 * они берут имя салона.
 *
 * # DRF-2115 — тройка стала посадкой (§50 п.5–6, отменяет §47.1)
 *
 * Владелец и администратор входят сюда сразу. Разделы, снятые с нижней
 * панели, — Команда, Услуги, Чаты с мастерами, Настройки — открываются из
 * аватара справа вверху (`AvatarSheet`, состав — `avatarSheetItemsFor`).
 * Системная кнопка «назад» MAX на трёх корнях спрятана, как у корней
 * любой поверхности: возвращаться отсюда некуда. Прежний жёсткий выход
 * на `/admin/settings` (вход был оттуда, решение 07.09) снят вместе с
 * входом.
 *
 * # Чего на рамке нет
 *
 * * **Стрелки выбора салона.** Макет её показывал; DRF-1235 велел убрать
 *   («убрать стрелку выбора салона, пока multi-salon не входит в P0»).
 *   Имя салона здесь — подпись, а не кнопка.
 * * **Меню «⋮» на каждом разделе.** DRF-1235 подтвердил для P0 ровно один
 *   его пункт — «Рабочее время», а это пункт «Расписания». С DRF-2936 экран
 *   рабочего времени есть, и меню стоит там, где его пункт имеет смысл:
 *   «Расписание» передаёт его в `titleActions` (DRF-2933 — в строке
 *   заголовка, как на макете). Сама рамка меню не рисует.
 *
 * Вложенные потоки пилота (создание записи, детали записи, очередь
 * диалогов) — отдельные экраны с явным возвратом, и DRF-1235 прямо
 * запрещает держать под ними нижнюю панель, поэтому эту рамку они не
 * используют.
 */
import { useEffect } from "react";
import type { ReactNode } from "react";

import { AvatarSheet } from "../../components/AvatarSheet";
import { SalonPilotTabBar } from "../../components/SalonPilotTabBar";
import type { MeResponse } from "../../lib/admin-api";
import { avatarSheetItemsFor } from "../../lib/avatar-sheet";
import { setBackButton } from "../../lib/max-sdk";

export function SalonPilotFrame({
  me,
  title,
  subtitle,
  actions,
  overline,
  titleActions,
  children,
}: {
  me: MeResponse;
  /** Название раздела — то же слово, что на вкладке. */
  title: string;
  /**
   * Строка под заголовком. Только то, что уже известно наверняка: на
   * «Сегодня» это дата, которую вернул сервер. Пока ответа нет —
   * `null`, и строки нет вовсе. Заранее посчитанная на устройстве дата
   * выглядела бы как ответ сервера и врала бы про часовой пояс салона.
   */
  subtitle?: string | null;
  /** Кнопки шапки слева от аватара. */
  actions?: ReactNode;
  /**
   * Строка НАД заголовком и кнопки справа от него — шапка «Расписания»
   * (DRF-2933, кадр 1 макета DRF-2854: дата над словом «Расписание»,
   * календарь и «⋯» в той же строке). Не заданы — заголовок рисуется как
   * прежде, у «Сегодня» и «Ayla» ничего не меняется.
   */
  overline?: string | null;
  titleActions?: ReactNode;
  children: ReactNode;
}) {
  // Корень поверхности: системной «назад» нечего показывать.
  useEffect(() => {
    setBackButton(false);
  }, []);

  return (
    <div className="screen">
      <div className="salon-pilot__topbar">
        <p className="salon-pilot__brand">{me.tenant.name}</p>
        {actions ? <div className="salon-pilot__actions">{actions}</div> : null}
        <AvatarSheet name={me.user.name} items={avatarSheetItemsFor(me)} />
      </div>
      {overline != null || titleActions ? (
        <div className="salon-pilot__titlerow">
          <div className="salon-pilot__titles">
            {overline != null ? <p className="salon-pilot__overline">{overline}</p> : null}
            <h1 className="screen__title salon-pilot__title">{title}</h1>
          </div>
          {titleActions ? <div className="salon-pilot__title-actions">{titleActions}</div> : null}
        </div>
      ) : (
        <h1 className="screen__title">{title}</h1>
      )}
      {subtitle ? <p className="salon-pilot__subtitle">{subtitle}</p> : null}
      <div className="screen__body">{children}</div>
      <SalonPilotTabBar />
    </div>
  );
}
