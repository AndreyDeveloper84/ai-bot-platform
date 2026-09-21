/**
 * Кадр 1 макета DRF-1320 — «Подходящий вариант для этого шага» (DRF-2178, Э-1).
 *
 * Экран ничего не выбирает сам: способ исполнения приносит резолвер
 * (`lib/booking-flow.ts::executionOption`), а длительность и цена берутся
 * из услуги зеркала. Нечего показать — кадра нет: человек уходит в
 * каталог, прежним честным путём, а не смотрит на пустой заголовок.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ScreenLayout } from "../components/ScreenLayout";
import {
  CATALOG_ROUTE,
  OPTION_CTA,
  OPTION_HEAD,
  OPTION_OTHER,
  OPTION_WHY_HEAD,
  PROVIDER_ROUTE,
  executionOption,
  serviceMeta,
  type ExecutionOption,
} from "../lib/booking-flow";
import { getCatalogBrowse } from "../lib/customer-booking";
import { backTo } from "../lib/screen-back";

export function ExecutionOptionScreen() {
  const navigate = useNavigate();
  const [option, setOption] = useState<ExecutionOption | null>(null);

  useEffect(() => {
    let alive = true;
    getCatalogBrowse()
      .then((browse) => {
        if (!alive) return;
        const found = executionOption(browse);
        // Нечего показать — и нечего объяснять: каталог остаётся открыт,
        // и это тот же путь, которым человек ходил до этого этапа.
        if (found === null) navigate(CATALOG_ROUTE);
        else setOption(found);
      })
      .catch(() => {
        if (alive) navigate(CATALOG_ROUTE);
      });
    return () => {
      alive = false;
    };
  }, [navigate]);

  // Родитель — каталог: отсюда человек пришёл бы, если бы шага не было.
  const back = backTo(CATALOG_ROUTE);

  if (option === null) {
    return (
      <ScreenLayout back={back}>
        <p role="status" aria-live="polite">
          Загружаю…
        </p>
      </ScreenLayout>
    );
  }

  const meta = serviceMeta(option.service);

  return (
    <ScreenLayout back={back}>
      <h1>{OPTION_HEAD}</h1>

      <article className="callout">
        <h2>{option.service.name}</h2>
        {/* Пусто — строки нет вовсе: «0 ₽» это не «цена неизвестна». */}
        {meta ? <p>{meta}</p> : null}
      </article>

      <h2>{OPTION_WHY_HEAD}</h2>
      <ul>
        {option.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>

      <button
        type="button"
        className="btn-primary"
        onClick={() => navigate(`${PROVIDER_ROUTE}?service=${option.service.id}`)}
      >
        {OPTION_CTA}
      </button>
      <button type="button" className="btn-secondary" onClick={() => navigate(CATALOG_ROUTE)}>
        {OPTION_OTHER}
      </button>
    </ScreenLayout>
  );
}
