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
import { MasterPhoto } from "../components/MasterPhoto";
import { OfflineBanner } from "../components/OfflineBanner";
import { StateError } from "../components/StateError";
import { useOnline } from "../hooks/useOnline";
import { fetchServices, type Service } from "../lib/api";
import { initialsOf } from "../lib/avatar-sheet";
import { masterServiceMeta, masterServices } from "../lib/booking-flow";
import {
  getCustomerMaster,
  type CustomerMaster,
} from "../lib/customer-booking";
import { masterCardP1Enabled } from "../lib/feature-flags";
import { NO_REVIEWS_LABEL, publicRating, reviewCountLabel } from "../lib/rating";
import { alignMaster, alignService, setEntryPoint, useBookingDraft } from "../state/booking";
import { backTo } from "../lib/screen-back";

/** Возврат (DRF-1493): в каталог — единственный вход в карточку мастера. */
const BACK = backTo("/customer/catalog");

type State =
  | { kind: "loading" }
  | { kind: "ok"; master: CustomerMaster }
  | { kind: "error"; err: unknown };

export const OTHER_MASTERS_LABEL = "Другие специалисты";

/**
 * DRF-2755 — заголовок блока услуг мастера. Слов владельца для него ещё нет,
 * и придумывать их нельзя: стоит метка-заполнитель, а блок закрыт флагом
 * `masterCardP1Enabled`. Пока здесь метка, флаг включать для людей нельзя.
 */
export const MASTER_SERVICES_HEAD = "[ТЕКСТ ВЛАДЕЛЬЦА: заголовок блока услуг мастера]";

export function CustomerMasterDetailScreen() {
  const online = useOnline();
  const navigate = useNavigate();
  const { masterId } = useParams<{ masterId: string }>();
  const [params] = useSearchParams();
  const serviceId = params.get("service");
  const draft = useBookingDraft();
  const [state, setState] = useState<State>({ kind: "loading" });
  // DRF-2755 — каталог услуг для блока «услуги мастера»; `null` — не
  // загружен (флаг выключен, ещё грузится, сервер не ответил). Блок тогда
  // не рисуется: карточка без списка честнее списка, собранного наугад.
  const p1 = masterCardP1Enabled();
  const [catalog, setCatalog] = useState<Service[] | null>(null);

  useEffect(() => {
    if (!p1) return;
    let alive = true;
    fetchServices()
      .then(({ services }) => {
        if (alive) setCatalog(services);
      })
      .catch(() => {
        if (alive) setCatalog(null);
      });
    return () => {
      alive = false;
    };
  }, [p1]);

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

  const offered = state.kind === "ok" ? masterServices(catalog, state.master.service_ids) : [];

  // DRF-2755 — запись на конкретную услугу мастера: услуга и мастер
  // фиксируются здесь, с именами, и уезжают на экран времени в адресе —
  // тем же швом, что у потока C05 (DRF-2752).
  function onChooseService(service: Service) {
    if (state.kind !== "ok" || !masterId) return;
    alignService(service.id, service.name);
    setEntryPoint("master");
    alignMaster(masterId, state.master.name);
    navigate(`/customer/masters/${masterId}/slots?service=${service.id}`);
  }

  function onChooseTime() {
    if (state.kind !== "ok" || !masterId) return;
    // DRF-2755 — услугу, которую этот мастер не оказывает, в запись не несём.
    // Список услуг мастера известен (флаг включён, каталог загружен), а
    // услуга из адреса или оставшаяся в черновике в него не входит: это
    // устаревший выбор, и безопаснее выбрать заново, чем открыть время
    // под неё. Каталог не загружен — судить не о чем, правило прежнее.
    const carried = serviceId || draft.serviceId;
    if (p1 && catalog !== null && carried && !offered.some((s) => s.id === carried)) {
      navigate("/customer/catalog");
      return;
    }
    // DRF-2752 — услуга из адреса главнее черновика. Раньше адрес учитывался
    // только при ПУСТОМ черновике: услуга, оставшаяся от прошлого выбора,
    // побеждала ту, что названа в адресе. Имя здесь неизвестно — экран
    // времени спросит его у сервера. Идёт первым: другая услуга начинает
    // путь заново, и источник входа с мастером ставятся уже в новый.
    if (serviceId) alignService(serviceId);
    // DRF-1484 — provenance: this flow originates at the master profile.
    setEntryPoint("master");
    // Другой мастер — время, выбранное у прежнего, больше не выбрано.
    alignMaster(masterId, state.master.name);
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
  const ratingValue = publicRating(m.rating, m.review_count);
  const rating = ratingValue === null ? null : ratingValue.toFixed(1);

  return (
    <ScreenLayout
      back={BACK}
      title={m.name}
      cta={
        <StickyCta onClick={onChooseTime} disabled={!online}>
          Выбрать время
        </StickyCta>
      }
    >
      <OfflineBanner online={online} />
      {/* DRF-2755 — большое фото мастера. Байты идут через прокси бота
          (DRF-2539); нет фото, ещё грузится или прокси отказал — инициалы. */}
      {p1 ? (
        <div className="customer-master__photo" data-testid="master-photo">
          <MasterPhoto
            src={m.photo_url}
            alt={m.name}
            fallback={<span aria-hidden="true">{initialsOf(m.name)}</span>}
          />
        </div>
      ) : null}
      <section className="customer-master__intro">
        <div className="customer-master__identity">
          <div className="customer-master__name">{m.name}</div>
          {/* DRF-2875 — оценка только вместе с числом отзывов; иначе слова владельца. */}
          {rating ? (
            <div
              className="customer-master__rating"
              aria-label={`Рейтинг ${rating}`}
            >
              <span aria-hidden="true">⭐ </span>
              {rating}
              <span className="customer-master__reviews" data-testid="master-reviews">
                {" "}({reviewCountLabel(m.review_count)})
              </span>
            </div>
          ) : (
            <div className="customer-master__reviews" data-testid="master-no-reviews">
              {NO_REVIEWS_LABEL}
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

      {/* DRF-2755 — услуги мастера с длительностью и ценой. Цена «от» — только
          из данных; `null` — цены нет в строке, а не «0 ₽». */}
      {p1 && offered.length > 0 ? (
        <section aria-labelledby="master-services-title">
          <h2 id="master-services-title" className="customer-master__section-title">
            {MASTER_SERVICES_HEAD}
          </h2>
          <ul className="customer-master__services">
            {offered.map((service) => {
              const meta = masterServiceMeta(service);
              return (
                <li key={service.id}>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={!online}
                    onClick={() => onChooseService(service)}
                  >
                    {meta ? `${service.name} · ${meta}` : service.name}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="master-slots-title">
        <h2 id="master-slots-title" className="customer-master__section-title">
          Ближайшие слоты
        </h2>
        <p style={{ color: "var(--c-text-secondary)", margin: 0 }}>
          Выбери удобное время — покажу свободные.
        </p>
      </section>

      {/* DRF-1778 (C05.3): «Другие специалисты» — с карточки, не только
          из ошибки. С известной услугой — выбор мастера под неё; без —
          каталог с той же секцией мастеров. Не marketplace: ни фильтров,
          ни сравнения. */}
      <div className="customer-master__others">
        <button
          type="button"
          className="goal-select__minor-action"
          onClick={() => {
            // DRF-2752 — то же правило: адрес главнее черновика.
            const svc = serviceId || draft.serviceId;
            if (svc) {
              if (serviceId) alignService(serviceId);
              navigate("/customer/book/master");
            } else {
              navigate("/customer/catalog");
            }
          }}
        >
          {OTHER_MASTERS_LABEL}
        </button>
      </div>

      {/* Secondary CTA «Сообщить по записи» — hidden in round-1 until
          the messaging route ships. `/customer/masters/{id}/message`
          has NO route registered → 404. Will be re-enabled with a
          real route per docs/design/policies/ayla-mediated-messaging.md
          when messaging UI lands. */}
    </ScreenLayout>
  );
}
