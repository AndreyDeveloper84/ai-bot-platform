/** F1-detail — single service. CTA «Подобрать время». */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, fetchService, type Service } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { StickyCta } from "../components/StickyCta";
import { DelayedSkeleton, Skeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import { formatDuration, formatMoney } from "../lib/format";
import { UNBOOKABLE_DETAIL } from "../components/UnbookableNote";
import { setService } from "../state/booking";

type State =
  | { kind: "loading" }
  | { kind: "ok"; service: Service }
  | { kind: "notfound" }
  | { kind: "error"; err: unknown };

export function ServiceDetailScreen() {
  const { serviceId } = useParams<{ serviceId: string }>();
  const navigate = useNavigate();
  const haptics = useHaptics();
  const [state, setState] = useState<State>({ kind: "loading" });

  useBackButton({ onBack: () => navigate(-1) });

  const load = useCallback(() => {
    if (!serviceId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    fetchService(serviceId)
      .then(({ service }) => {
        if (!cancelled) setState({ kind: "ok", service });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ kind: "notfound" });
        } else {
          setState({ kind: "error", err });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [serviceId]);

  useEffect(() => load(), [load]);

  function onContinue() {
    if (state.kind !== "ok") return;
    // Belt-and-braces: the CTA is not rendered for an unbookable service,
    // so this can only be reached by a stale closure. Refuse anyway —
    // the draft must never carry a service nobody can perform.
    if (!state.service.is_bookable) return;
    haptics.selection();
    setService(state.service.id, state.service.name);
    navigate("/book/master");
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Услуга">
        <DelayedSkeleton loading>
          <Skeleton width="70%" height="1.6em" />
          <div style={{ marginTop: "var(--s-3)" }}>
            <Skeleton height="0.9em" />
          </div>
          <div style={{ marginTop: "var(--s-2)" }}>
            <Skeleton height="0.9em" width="80%" />
          </div>
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "notfound") {
    return (
      <ScreenLayout title="Услуга">
        <div className="callout">
          <p style={{ margin: 0 }}>Не нашлось. Возможно, удалили.</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/catalog", { replace: true })}
          >
            К услугам
          </button>
        </div>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout title="Услуга">
        <StateError err={state.err} onRetry={load} screenId="service-detail" />
      </ScreenLayout>
    );
  }

  const s = state.service;
  // DRF-1164 — this screen is the ONE door into the booking flow for a
  // service (`/catalog/:serviceId` is where both catalog surfaces and the
  // wellness picks land, and a bot deep-link by service id arrives here
  // too). So the CTA is withheld here rather than only greyed out on the
  // card: no CTA, no `setService()`, no `/book/master` — the dead end the
  // customer used to fall into is unreachable from the service side.
  // The page itself stays — description and contraindications are still
  // worth reading, and the callout says why booking is off.
  const unbookable = !s.is_bookable;
  return (
    <ScreenLayout
      title={s.name}
      cta={
        unbookable ? undefined : (
          <StickyCta onClick={onContinue}>Подобрать время</StickyCta>
        )
      }
    >
      {unbookable && (
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>{UNBOOKABLE_DETAIL}</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/catalog")}
          >
            Другие услуги
          </button>
        </div>
      )}
      <div className="confirm-card">
        <dl>
          <dt>Длительность и цена</dt>
          <dd>
            {formatDuration(s.duration_min)}
            {s.duration_min && s.price_from ? " • " : ""}
            {formatMoney(s.price_from)}
          </dd>
          {s.short_description && (
            <>
              <dt>Описание</dt>
              <dd>{s.short_description}</dd>
            </>
          )}
          {s.contraindications && (
            <>
              <dt>Противопоказания</dt>
              <dd>{s.contraindications}</dd>
            </>
          )}
        </dl>
      </div>
    </ScreenLayout>
  );
}
