/**
 * F2 — Master detail screen, service-scoped.
 *
 * Spec: `docs/screens/customer-booking-flow.md` §4 (§4.1 voice / §4.2
 * states).
 *
 * Voice (Tau §8 F2):
 *   - «Что она делает» (not «Услуги мастера»)
 *   - «Цены» (not «Стоимость»)
 *   - «Ближайшие слоты» (not «Доступное время для записи»)
 *   - «Сообщить по записи» CTA (founder F1 unification — NEVER
 *     «Чат с мастером» / «Написать мастеру»).
 *   - Primary CTA: «Выбрать время» → F3.
 *
 * Master substitution edge per Tau §4.2 row 3 («out of slots next 14
 * days») is handled in F3 (the slots screen) — F2 just navigates.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { DelayedSkeleton, MasterCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  getCustomerMaster,
  type CustomerMaster,
} from "../lib/customer-booking";
import { publicRating } from "../lib/rating";
import { setEntryPoint, setMaster, setService, useBookingDraft } from "../state/booking";
import { backTo } from "../lib/screen-back";

/** Возврат (DRF-1493): в каталог — единственный вход в карточку мастера. */
const BACK = backTo("/customer/catalog");

type State =
  | { kind: "loading" }
  | { kind: "ok"; master: CustomerMaster }
  | { kind: "error"; err: unknown };

export function CustomerMasterDetailScreen() {
  const navigate = useNavigate();
  const { masterId } = useParams<{ masterId: string }>();
  const [params] = useSearchParams();
  const serviceId = params.get("service");
  const draft = useBookingDraft();
  const [state, setState] = useState<State>({ kind: "loading" });


  const load = useCallback(() => {
    if (!masterId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    getCustomerMaster(masterId)
      .then(({ master }) => {
        if (!cancelled) setState({ kind: "ok", master });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [masterId]);

  useEffect(() => load(), [load]);

  function onChooseTime() {
    if (state.kind !== "ok" || !masterId) return;
    // DRF-1484 — provenance: this flow originates at the master profile.
    setEntryPoint("master");
    setMaster(masterId, state.master.name);
    // Pre-fill service in draft if URL param available — keeps the
    // F3 → F4 chain consistent.
    if (serviceId && !draft.serviceId) {
      // Service name unknown here; the draft only needs the id for
      // the slots query. F4 will fetch service name from the booking
      // response if necessary.
      setService(serviceId, "");
    }
    navigate(`/customer/masters/${masterId}/slots`);
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Мастер">
        <DelayedSkeleton loading>
          <MasterCardSkeleton />
          <MasterCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={BACK} title="Мастер">
        <StateError err={state.err} onRetry={load} screenId="customer-master-detail" />
      </ScreenLayout>
    );
  }

  const m = state.master;
  // DRF-1224 — same 1..5 domain rule as the catalog card.
  const ratingValue = publicRating(m.rating);
  const rating = ratingValue === null ? null : ratingValue.toFixed(1);

  return (
    <ScreenLayout
      back={BACK}
      title={m.name}
      cta={
        <StickyCta onClick={onChooseTime}>
          Выбрать время
        </StickyCta>
      }
    >
      <section className="customer-master__intro">
        <div className="customer-master__identity">
          <div className="customer-master__name">{m.name}</div>
          {rating && (
            <div
              className="customer-master__rating"
              aria-label={`Рейтинг ${rating}`}
            >
              <span aria-hidden="true">⭐ </span>
              {rating}
            </div>
          )}
          {m.specialization && (
            <div className="customer-master__spec">{m.specialization}</div>
          )}
          {m.experience && (
            <div className="customer-master__exp">{m.experience}</div>
          )}
        </div>
      </section>

      {m.bio && (
        <section aria-labelledby="master-bio-title">
          <h2 id="master-bio-title" className="customer-master__section-title">
            Что она делает
          </h2>
          <p className="customer-master__bio">{m.bio}</p>
        </section>
      )}

      <section aria-labelledby="master-slots-title">
        <h2 id="master-slots-title" className="customer-master__section-title">
          Ближайшие слоты
        </h2>
        <p style={{ color: "var(--c-text-secondary)", margin: 0 }}>
          Выбери удобное время — покажу свободные.
        </p>
      </section>

      {/* Secondary CTA «Сообщить по записи» — hidden in round-1 until
          the messaging route ships. `/customer/masters/{id}/message`
          has NO route registered → 404. Will be re-enabled with a
          real route per docs/design/policies/ayla-mediated-messaging.md
          when messaging UI lands. */}
    </ScreenLayout>
  );
}
