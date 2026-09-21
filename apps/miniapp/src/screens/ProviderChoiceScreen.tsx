/**
 * Кадр 2 макета DRF-1320 — «Лучше всего подходит» (DRF-2178, Э-1; DRF-1775).
 *
 * Один лучший специалист и до двух других — в порядке решения резолвера,
 * не нашей сортировки. Причина под именем («Почему она») — из причин
 * кандидата дословно: лист DRF-1775 считал их заблокированными D11, но
 * транзит `reasonCodes`/`reasons` сделан в DRF-2174, и гейт «без
 * displayable-причины кандидат сюда не доходит» встроен в него.
 *
 * Кандидатов нет — кадра нет: остаётся прежний полный список мастеров.
 */
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ScreenLayout } from "../components/ScreenLayout";
import {
  ALL_PROVIDERS_ROUTE,
  OPTION_ROUTE,
  PROVIDER_CTA,
  PROVIDER_HEAD,
  PROVIDER_OTHERS_HEAD,
  PROVIDER_OTHER_LINK,
  PROVIDER_WHY_HEAD,
  providerChoice,
  providerMeta,
  type ProviderChoice,
} from "../lib/booking-flow";
import { getCatalogBrowse } from "../lib/customer-booking";
import { backTo } from "../lib/screen-back";

export function ProviderChoiceScreen() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const serviceId = params.get("service") ?? "";
  const [choice, setChoice] = useState<ProviderChoice | null>(null);

  const allProviders = serviceId
    ? `${ALL_PROVIDERS_ROUTE}?service=${serviceId}`
    : ALL_PROVIDERS_ROUTE;

  useEffect(() => {
    let alive = true;
    getCatalogBrowse()
      .then((browse) => {
        if (!alive) return;
        const found = providerChoice(browse);
        // Выбирать не из кого — прежний путь: полный список мастеров.
        if (found === null) navigate(allProviders);
        else setChoice(found);
      })
      .catch(() => {
        if (alive) navigate(allProviders);
      });
    return () => {
      alive = false;
    };
  }, [navigate, allProviders]);

  const back = backTo(OPTION_ROUTE);

  if (choice === null) {
    return (
      <ScreenLayout back={back}>
        <p role="status" aria-live="polite">
          Загружаю…
        </p>
      </ScreenLayout>
    );
  }

  const { best, others } = choice;
  const bestMeta = providerMeta(best.master);

  return (
    <ScreenLayout back={back}>
      <h1>{PROVIDER_HEAD}</h1>

      <article className="callout">
        <h2>{best.master.name}</h2>
        {/* Нет рейтинга и отзывов — строки нет, а не «null» в скобках. */}
        {bestMeta ? <p>{bestMeta}</p> : null}

        <h3>{PROVIDER_WHY_HEAD}</h3>
        <ul>
          {best.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </article>

      <button
        type="button"
        className="btn-primary"
        onClick={() =>
          navigate(
            serviceId
              ? `/customer/masters/${best.master.id}/slots?service=${serviceId}`
              : `/customer/masters/${best.master.id}/slots`,
          )
        }
      >
        {PROVIDER_CTA(best.master.name)}
      </button>
      <button type="button" className="btn-secondary" onClick={() => navigate(allProviders)}>
        {PROVIDER_OTHER_LINK}
      </button>

      {others.length > 0 ? (
        <section>
          <h2>{PROVIDER_OTHERS_HEAD}</h2>
          {others.map((option) => {
            const meta = providerMeta(option.master);
            return (
              <button
                key={option.master.id}
                type="button"
                className="btn-secondary"
                onClick={() =>
                  navigate(
                    serviceId
                      ? `/customer/masters/${option.master.id}/slots?service=${serviceId}`
                      : `/customer/masters/${option.master.id}/slots`,
                  )
                }
              >
                {meta ? `${option.master.name} · ${meta}` : option.master.name}
              </button>
            );
          })}
        </section>
      ) : null}
    </ScreenLayout>
  );
}
