/**
 * «Принимаю записи / Не принимаю» на дашборде мастера (DRF-1845, карта
 * кабинета D03).
 *
 * Флаг живёт в каталоге (`SpecialistProfile.is_booking_enabled`); экран
 * читает и пишет его через `/accepting-bookings` и рисует только то, что
 * каталог прочёл. Самозагружаемый, как `PayoutPreviewCard`: если прочитать
 * не удалось (профиль не связан, каталог недоступен) — переключателя нет.
 * Кнопка, за которой нет ручки, не рисуется.
 *
 * Слова — честные по времени: в каталоге пауза действует сразу, а бот узнаёт
 * о ней на следующем синке (≤15 мин) — так и сказано. Уже созданные записи
 * пауза не отменяет.
 */
import { useEffect, useState } from "react";

import { ApiError } from "../lib/api";
import {
  getAcceptingBookings,
  setAcceptingBookings,
  type AcceptingBookingsResponse,
} from "../lib/master-api";

export const ACCEPTING_COPY = {
  label: "Принимаю записи",
  onHint: "Клиенты видят вас и могут записаться.",
  offHint:
    "Новые записи на паузе. Клиенты перестанут видеть вас в течение 15 минут; уже созданные записи остаются.",
  notPublished: "Принимать записи можно после публикации профиля.",
  failed: "Не удалось сохранить — попробуйте ещё раз.",
} as const;

export function AcceptingBookingsToggle() {
  const [state, setState] = useState<AcceptingBookingsResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAcceptingBookings()
      .then((data) => {
        if (!cancelled) setState(data);
      })
      .catch((err: unknown) => {
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn("[accepting-bookings] hidden — read failed", err);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!state) return null;
  const on = state.accepting_bookings;

  const toggle = async () => {
    setSaving(true);
    setNotice(null);
    try {
      setState(await setAcceptingBookings(!on));
    } catch (err: unknown) {
      setNotice(
        err instanceof ApiError && err.slug === "profile_not_active"
          ? ACCEPTING_COPY.notPublished
          : ACCEPTING_COPY.failed,
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="master-dashboard__section" aria-labelledby="m1-accepting">
      <div className="accepting-bookings__row">
        <span id="m1-accepting" className="accepting-bookings__label">
          {ACCEPTING_COPY.label}
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby="m1-accepting"
          className={`accepting-bookings__switch${on ? " accepting-bookings__switch--on" : ""}`}
          disabled={saving}
          onClick={toggle}
        >
          <span className="accepting-bookings__knob" aria-hidden="true" />
        </button>
      </div>
      <p className="master-dashboard__today-line">
        {on ? ACCEPTING_COPY.onHint : ACCEPTING_COPY.offHint}
      </p>
      {notice ? (
        <p className="master-dashboard__today-line" role="status">
          {notice}
        </p>
      ) : null}
    </section>
  );
}
