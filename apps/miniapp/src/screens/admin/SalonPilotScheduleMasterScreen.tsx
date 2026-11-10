/**
 * «Мастер» — день одного мастера из «Расписания» салона (DRF-2933, кадр 12
 * макета DRF-2854 «Быстрый переход к мастеру»).
 *
 * Адрес: `/admin/schedule/master/:masterId?date=YYYY-MM-DD`. Сюда ведёт
 * нажатие на мастера в сетке и в ленте. Экран читает и ведёт дальше, как и
 * само «Расписание»: запись → карточка, свободное окно → ручная запись с
 * мастером, датой и окном, блок → «Рабочее время» этой даты.
 *
 * Две вкладки, как на кадре: «Расписание» — день списком (`MasterDayPanel`,
 * тот же, что на «Расписании» с одним выбранным мастером) и «О мастере» —
 * то, что о нём уже известно, только чтение, и ссылка на его карточку: там
 * недельный график и его подтверждение.
 *
 * Записи берутся из дня салона — те же, что в сетке, со всеми статусами;
 * часы, блоки и свободные окна — из `day-schedule` мастера. Мастера в дне
 * салона нет (архив, чужой адрес) — записей нет, а не ошибка: остальное
 * скажет `day-schedule`.
 *
 * Нижней панели нет: это вложенный экран с явным возвратом (DRF-1235).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { SystemState } from "../../components/OperationalSystemState";
import { useOnline } from "../../hooks/useOnline";
import { useMasterDay, useSalonDay } from "../../hooks/useSalonSchedule";
import { useScreenBack } from "../../hooks/useScreenBack";
import { getMasterDetail, type MasterDetail } from "../../lib/admin-api";
import { signalReady } from "../../lib/max-sdk";
import {
  SS_COPY,
  closureNotices,
  columnVisit,
  dateLine,
  dateParam,
  newBookingPath,
  salonNow,
  scheduleMasterPath,
  schedulePath,
} from "../../lib/salon-schedule";
import { workingTimeDayPath } from "../../lib/salon-working-time";
import { backTo } from "../../lib/screen-back";
import { MasterDayPanel } from "./ScheduleMasterDay";
import { WtAvatar, WtHeader, WtLoadError } from "./WorkingTimeParts";

type Tab = "schedule" | "about";

type DetailPhase =
  | { kind: "loading" }
  | { kind: "ready"; detail: MasterDetail }
  | { kind: "error"; err: unknown };

export function SalonPilotScheduleMasterScreen() {
  const { masterId = "" } = useParams<{ masterId: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const online = useOnline();
  const wanted = dateParam(params.get("date"));
  const back = useScreenBack(backTo(schedulePath(wanted)));

  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const phase = useSalonDay(wanted, attempt);
  const data = phase.kind === "ready" ? phase.data : null;
  const refreshErr = phase.kind === "ready" ? phase.refreshErr : null;
  const refreshing = phase.kind === "ready" && phase.refreshing;
  const date = data?.day.date ?? wanted;

  const [tab, setTab] = useState<Tab>("schedule");
  const [stale, setStale] = useState(false);
  const [about, setAbout] = useState<DetailPhase>({ kind: "loading" });
  const [aboutAttempt, setAboutAttempt] = useState(0);

  // День мастера читается ПОСЛЕ дня салона: перечитали день — перечитали и
  // окна, а не два раза подряд на одном открытии.
  const masterDay = useMasterDay(masterId, data ? date : null, data?.loadedAt ?? 0);

  useEffect(() => {
    signalReady();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setAbout({ kind: "loading" });
    getMasterDetail(masterId, { signal: controller.signal }).then(
      (detail) => {
        if (!controller.signal.aborted) setAbout({ kind: "ready", detail });
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setAbout({ kind: "error", err });
      },
    );
    return () => controller.abort();
  }, [masterId, aboutAttempt]);

  // Возврат на экран перечитывает день — как на самом «Расписании».
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") retry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [retry]);

  // Перечитали день — пометка «могло измениться» больше не про него.
  const loadedAt = data?.loadedAt ?? 0;
  useEffect(() => {
    setStale(false);
  }, [loadedAt]);

  const dayMaster = data?.day.masters.find((m) => m.master_id === masterId) ?? null;
  const visits = useMemo(
    () => (data && dayMaster ? dayMaster.visits.map((v) => columnVisit(v, data.day.date)) : []),
    [data, dayMaster],
  );
  const closures = useMemo(() => closureNotices(data?.frame ?? null), [data]);
  const detail = about.kind === "ready" ? about.detail : null;
  const name = detail?.name ?? dayMaster?.name ?? "";
  const active = dayMaster?.is_active ?? detail?.is_active ?? false;

  return (
    <div className="screen wt-screen wt-screen--nested">
      <WtHeader title={SS_COPY.master.title} onBack={back} onClose={back} />
      <div className="wt-body">
        {!online ? <SystemState kind="offline" /> : null}

        {name ? (
          <div className="ss-who">
            <WtAvatar name={name} photoUrl={detail?.photo_url} />
            <span className="ss-who__text">
              <span className="ss-who__name">{name}</span>
              {detail?.specialization ? <span className="ss-who__spec">{detail.specialization}</span> : null}
            </span>
          </div>
        ) : null}

        <div className="ss-segment" role="tablist" aria-label={SS_COPY.master.title}>
          {(["schedule", "about"] as const).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`ss-tab-${t}`}
              aria-selected={tab === t}
              aria-controls={`ss-tabpanel-${t}`}
              className={`ss-segment__item${tab === t ? " ss-segment__item--on" : ""}`}
              onClick={() => setTab(t)}
            >
              {SS_COPY.master[t]}
            </button>
          ))}
        </div>

        {tab === "schedule" ? (
          <div role="tabpanel" id="ss-tabpanel-schedule" aria-labelledby="ss-tab-schedule" className="ss-tabpanel">
            {phase.kind === "loading" ? <SystemState kind="loading" lines={4} /> : null}
            {phase.kind === "error" ? <WtLoadError what="schedule" err={phase.err} onRetry={retry} /> : null}

            {data && date ? (
              <>
                <h2 className="ss-day-title">{dateLine(date)}</h2>

                {refreshErr != null ? (
                  <SystemState
                    kind="load_error"
                    what="schedule"
                    hasData
                    err={refreshErr}
                    busy={refreshing}
                    onRetry={retry}
                  />
                ) : null}

                <p className="ss-asof">
                  <span>{SS_COPY.asOf(salonNow(data.day.timezone, new Date(data.loadedAt)).hm)}</span>
                  <button type="button" className="ss-asof__refresh" disabled={refreshing} onClick={retry}>
                    {SS_COPY.refresh}
                  </button>
                </p>

                {stale ? (
                  <div className="ss-stale" role="alert">
                    <SystemState kind="stale" />
                    <p className="ss-note">{SS_COPY.staleWindows}</p>
                  </div>
                ) : null}

                {closures.map((closure, i) => (
                  <div key={`${closure.text}-${i}`} className="callout" role="status">
                    <p className="ss-callout__text">
                      {closure.reason ? `${closure.text} · ${closure.reason}` : closure.text}
                    </p>
                  </div>
                ))}

                <MasterDayPanel
                  phase={masterDay}
                  date={date}
                  timezone={data.day.timezone}
                  visits={visits}
                  serviceIds={[]}
                  salonClosed={closures.length > 0}
                  masterActive={active}
                  online={online && refreshErr == null}
                  onVisit={(id) =>
                    navigate(`/admin/bookings/${encodeURIComponent(id)}`, {
                      state: { from: scheduleMasterPath(masterId, date) },
                    })
                  }
                  onBook={(window) =>
                    navigate(
                      newBookingPath({
                        date,
                        masterId,
                        from: window.start,
                        to: window.end,
                        returnTo: "schedule-master",
                      }),
                    )
                  }
                  onBlock={() => navigate(workingTimeDayPath(masterId, date))}
                  onStale={() => setStale(true)}
                  onRetry={retry}
                />
              </>
            ) : null}
          </div>
        ) : (
          <div role="tabpanel" id="ss-tabpanel-about" aria-labelledby="ss-tab-about" className="ss-tabpanel">
            {about.kind === "loading" ? <SystemState kind="loading" lines={3} /> : null}
            {about.kind === "error" ? (
              <SystemState
                kind="load_error"
                what="profile"
                err={about.err}
                onRetry={() => setAboutAttempt((n) => n + 1)}
              />
            ) : null}
            {detail ? <AboutMaster detail={detail} onCard={() => navigate(`/admin/team/${encodeURIComponent(masterId)}`)} /> : null}
          </div>
        )}
      </div>
    </div>
  );
}

function AboutMaster({ detail, onCard }: { detail: MasterDetail; onCard: () => void }) {
  const facts: Array<[string, string]> = [];
  if (detail.specialization.trim()) facts.push([SS_COPY.master.specialization, detail.specialization.trim()]);
  if (detail.experience.trim()) facts.push([SS_COPY.master.experience, detail.experience.trim()]);
  if (detail.bio.trim()) facts.push([SS_COPY.master.bio, detail.bio.trim()]);
  if (detail.services.length > 0) {
    facts.push([SS_COPY.master.services, detail.services.map((svc) => svc.name).join(", ")]);
  }
  return (
    <div className="ss-about">
      {facts.length === 0 ? <SystemState kind="empty" text={SS_COPY.master.empty} /> : null}
      {facts.length > 0 ? (
        <dl className="ss-about__list">
          {facts.map(([term, value]) => (
            <div key={term} className="ss-about__row">
              <dt className="ss-about__term">{term}</dt>
              <dd className="ss-about__value">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <p className="ss-note">{SS_COPY.master.cardNote}</p>
      <button type="button" className="btn-secondary wt-btn" onClick={onCard}>
        {SS_COPY.master.card}
      </button>
    </div>
  );
}
