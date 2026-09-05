/**
 * Standard Mini App screen frame.
 *
 * Provides the content area with bottom padding pre-computed for the
 * sticky CTA + safe area. Children render inside `.screen`. The CTA
 * (when present) is rendered as a sibling so it floats above content.
 *
 * Возврат — часть каркаса, а не забота экрана (DRF-1493). Пока его
 * здесь не было, кнопку «назад» рисовал себе каждый экран сам, и набор
 * разъехался: у половины клиентских экранов её просто не оказалось.
 * Теперь `back` — обязательное поле: экран либо называет родителя,
 * либо объявляет себя корнем с причиной. Промолчать нельзя — не
 * соберётся.
 */

import type { ReactNode } from "react";

import { useScreenBack } from "../hooks/useScreenBack";
import type { BackIntent } from "../lib/screen-back";

interface Props {
  title?: string;
  /**
   * Куда ведёт возврат — объявляется КАЖДЫМ экраном (DRF-1493).
   *
   * `backTo("/адрес")` — есть родитель; `screenRoot("почему")` — выше
   * некуда; `backToStep(fn)` — шаг назад внутри экрана. Поля без
   * значения по умолчанию: молчание не должно быть валидным
   * состоянием, иначе дыра открывается заново на первом же новом
   * экране.
   */
  back: BackIntent;
  children: ReactNode;
  cta?: ReactNode;
  /**
   * Панель занимает две строки, а не одну (DRF-1469).
   *
   * Отступ снизу у `.screen` посчитан под панель в один ряд. Экран,
   * который положил в панель второе действие — поверхность цели с
   * выходом «Сменить режим», — обязан сказать об этом здесь, иначе
   * закреплённая панель накроет последние строки документа. Панель и
   * отступ живут в разных узлах дерева, и связать их одним только CSS
   * нечем: `.cta-bar` — сосед ПОСЛЕ `.screen`.
   */
  tallCta?: boolean;
}

export function ScreenLayout({ title, children, cta, tallCta, back }: Props) {
  const onBack = useScreenBack(back);
  return (
    <>
      <main className={tallCta ? "screen screen--tall-cta" : "screen"}>
        {onBack ? (
          <button
            type="button"
            className="screen__back"
            aria-label="Назад"
            onClick={onBack}
          >
            <span aria-hidden="true">←</span>
          </button>
        ) : null}
        {title ? <h1 className="screen__title">{title}</h1> : null}
        <div className="screen__body">{children}</div>
      </main>
      {cta}
    </>
  );
}
