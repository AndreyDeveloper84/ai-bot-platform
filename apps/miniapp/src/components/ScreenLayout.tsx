/**
 * Standard Mini App screen frame.
 *
 * Provides the content area with bottom padding pre-computed for the
 * sticky CTA + safe area. Children render inside `.screen`. The CTA
 * (when present) is rendered as a sibling so it floats above content.
 */

import type { ReactNode } from "react";

interface Props {
  title?: string;
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

export function ScreenLayout({ title, children, cta, tallCta }: Props) {
  return (
    <>
      <main className={tallCta ? "screen screen--tall-cta" : "screen"}>
        {title ? <h1 className="screen__title">{title}</h1> : null}
        <div className="screen__body">{children}</div>
      </main>
      {cta}
    </>
  );
}
