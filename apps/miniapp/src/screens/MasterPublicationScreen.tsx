/**
 * Экран 08 — отправка профиля на проверку (DRF-1818, M26; макет 8, кадры 8.1–8.4).
 *
 * Состояние — только из ответа каталога (`GET /publication/status`, M5 → M4):
 * ACTIVE → «Профиль опубликован!»; PENDING → «Профиль отправлен на проверку»
 * (D2 → б: ACTIVE ставит модератор, срок не обещаем); черновик + READY → 8.1;
 * черновик + NOT_READY → 8.2 с пунктами по кодам `missing`. Готовность экран
 * не пересчитывает; переходы к редакторам — `deep_link` readiness бота по
 * тому же ключу секции, своих маршрутов редакторов здесь нет.
 *
 * Отправка (8.3): один `command_id` на попытку; пока запрос идёт, кнопка
 * заблокирована и второй тап POST не шлёт. Обрыв, таймаут, 5xx — исход
 * неизвестен: экран предлагает «Проверить статус» (GET), а не второй POST;
 * повтор после проверки идёт с тем же ключом, и каталог его не выполнит дважды.
 *
 * Отличия от макета — ради честности (перечислены в PR): «Отправить профиль
 * на проверку» вместо «Опубликовать» (D2); нет шагов «проверяем профиль /
 * услуги / расписание» и слов о времени — шагов у сервера нет; 8.4 —
 * текстовая сводка вместо карточки до M22; «Популярные услуги» → «Услуги»
 * (данных о спросе нет); каждый рабочий день печатается отдельно; города в
 * сводке нет — у соло-мастера его даёт только место, а указать место до M11
 * нельзя.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

// Загрузка / ошибка загрузки — мастерский SystemState (DRF-2194), не клиентский StateError.
import { SystemState } from "../components/master/SystemState";
import { useSelfService } from "../hooks/useMasterAvatarItems";
import { ApiError } from "../lib/api";
import { formatDuration, formatMoney } from "../lib/format";
import {
  getMasterMe,
  getOnboardingReadiness,
  getPublicationStatus,
  getServiceSelection,
  getWorkingHours,
  listCanonGapRequests,
  publishProfile,
  type CanonGapRequest,
  type MasterMeResponse,
  type OnboardingReadiness,
  type PublicationMissingItem,
  type PublicationStatus,
  type ServiceSelectionState,
  type WorkingHoursDay,
} from "../lib/master-api";
import { setBackButton, signalReady } from "../lib/max-sdk";
import { SELECT_PATH } from "./MasterServicesScreen";
import { HOME_ROUTE, READINESS_ITEM_LABELS, SETUP_ROUTE } from "./MasterSetupLandingScreen";
import { DAY_LABELS } from "./MasterWorkingHoursScreen";

/** Сколько ждём ответа на отправку, прежде чем назвать исход неизвестным. */
export const PUBLISH_TIMEOUT_MS = 20_000;

export const PUBLICATION_COPY = {
  readyTitle: "Всё готово к отправке на проверку",
  readyLead:
    "Проверьте основные настройки. После проверки модератором клиенты смогут найти вас и записываться на доступные услуги.",
  notReadyTitle: "Почти готово",
  notReadyLead: "Заполните недостающие пункты, чтобы отправить профиль на проверку.",
  notReadyNote: "После заполнения всех обязательных пунктов вы сможете отправить профиль на проверку.",
  checklistLabel: "Проверка перед отправкой",
  submit: "Отправить профиль на проверку",
  sending: "Отправляем…",
  checking: "Проверяем…",
  backToSetup: "Вернуться к настройкам",
  continueSetup: "Продолжить настройку",
  uncertainTitle: "Не уверены, отправился ли профиль?",
  uncertainLead:
    "Если соединение прервалось, профиль уже мог уйти на проверку. Проверим его текущее состояние.",
  checkStatus: "Проверить статус",
  pendingTitle: "Профиль отправлен на проверку",
  pendingLead: "Клиенты увидят профиль после проверки модератором.",
  activeTitle: "Профиль опубликован!",
  activeLead: "Теперь клиенты смогут найти вас в Ayla и записываться на доступные услуги.",
  daysLabel: "Рабочие дни",
  toCabinet: "Перейти в кабинет",
  addServices: "Добавить ещё услуги",
  servicesTitle: "Услуги",
  reviewTitle: "Услуги на проверке",
  reviewNote: "Эти услуги пока не видны клиентам и не мешают отправить профиль.",
  reviewAfter: "Услуги на проверке появятся у клиентов после подтверждения.",
  refusedTitle: "Отправить профиль сейчас нельзя",
  retry: "Повторить",
  profileDone: "Фото и имя заполнены",
  locationDone: "Место указано",
  identityLabel: "Подтверждение личности",
  locationUnavailable: "Указать место в приложении пока нельзя — напишите в поддержку.",
} as const;

/** Текст пункта 8.2 — по коду каталога (M4 `publication_readiness`). */
export const PUBLICATION_MISSING_TEXT: Record<string, string> = {
  photo_missing: "Добавьте фотографию, которую увидят клиенты.",
  display_name_missing: "Укажите имя, которое увидят клиенты.",
  no_configured_service: "Нет ни одной услуги с ценой и длительностью.",
  location_not_assigned: "Место работы ещё не указано.",
  // DRF-1957: к проверке не пускает только недействительное место; «не подтверждено»
  // мастеру не показывается — место подтверждает модератор при одобрении профиля.
  location_inactive: "Место работы помечено недействительным — укажите актуальное место.",
  no_working_day: "Не задано рабочее время.",
  identity_not_linked: "Личность ещё не подтверждена оператором.",
};
const MISSING_FALLBACK = "Пункт не заполнен.";

export const PUBLICATION_ACTION_LABEL: Record<string, string> = {
  profile: "Заполнить профиль",
  services: "Настроить услуги",
  hours: "Настроить расписание",
};

/** Отказы по имени (`error`), каждый своим текстом; статус ответа их не различает. */
export const PUBLICATION_REFUSAL_TEXT: Record<string, string> = {
  catalog_profile_unresolved: "Профиль ещё не заведён в каталоге. Повторите позже или напишите в поддержку.",
  not_linked: "Профиль ещё не связан с каталогом: отправить его можно после подтверждения личности.",
  specialist_not_found: "Профиль мастера не найден в каталоге — напишите в поддержку.",
  salon_publication_owner_managed: "Профиль мастера салона публикует владелец салона.",
  no_workspace_tenant: "Рабочее пространство ещё не создано — напишите в поддержку.",
  publication_refused: "Отправка на проверку сейчас недоступна.",
};

const SECTIONS = ["profile", "services", "location", "hours"] as const;

export function ruPluralServices(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "услуг";
  if (mod10 === 1) return "услуга";
  if (mod10 >= 2 && mod10 <= 4) return "услуги";
  return "услуг";
}

/** Ключ одной попытки отправки (UUID v4). */
export function newCommandId(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Каждый рабочий день отдельно: «Вт 10:00–19:00» — без склейки «Вт–Сб», которая спрятала бы разные часы. */
export function workingDayLines(days: WorkingHoursDay[] | null): string[] {
  if (!days) return [];
  return [...days]
    .filter((d) => d.is_working_day && d.start_time && d.end_time)
    .sort((a, b) => a.day_of_week - b.day_of_week)
    .map((d) => `${DAY_LABELS[d.day_of_week] ?? ""} ${d.start_time}–${d.end_time}`);
}

function refusalSlug(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  return Object.prototype.hasOwnProperty.call(PUBLICATION_REFUSAL_TEXT, err.slug) ? err.slug : null;
}

function groupMissing(items: PublicationMissingItem[]): Map<string, PublicationMissingItem[]> {
  const bySection = new Map<string, PublicationMissingItem[]>();
  for (const item of items) bySection.set(item.section, [...(bySection.get(item.section) ?? []), item]);
  return bySection;
}

function sectionLink(readiness: OnboardingReadiness | null, section: string): string | null {
  const item = readiness?.items.find((i) => i.key === section);
  return item && item.state !== "unavailable" ? item.deep_link : null;
}

interface Loaded {
  status: PublicationStatus;
  readiness: OnboardingReadiness | null;
  selection: ServiceSelectionState | null;
  hours: WorkingHoursDay[] | null;
  requests: CanonGapRequest[];
  me: MasterMeResponse | null;
}

type Phase =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "refused"; slug: string }
  | { kind: "loaded"; data: Loaded };

type SendState = "idle" | "sending" | "uncertain" | "checking";

export function MasterPublicationScreen() {
  const navigate = useNavigate();
  const selfService = useSelfService();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [send, setSend] = useState<SendState>("idle");
  const inFlight = useRef(false);
  const attempt = useRef<string | null>(null);

  useEffect(() => {
    setBackButton(false);
    signalReady();
  }, []);

  const load = async (quiet = false) => {
    if (!quiet) setPhase({ kind: "loading" });
    try {
      const [status, readiness, selection, hours, requests, me] = await Promise.all([
        getPublicationStatus(),
        // Остальное — сводка и переходы; без них экран честно показывает меньше.
        getOnboardingReadiness().catch(() => null),
        getServiceSelection().catch(() => null),
        getWorkingHours()
          .then((r) => r.schedule)
          .catch(() => null),
        listCanonGapRequests()
          .then((r) => r.requests)
          .catch((): CanonGapRequest[] => []),
        getMasterMe().catch(() => null),
      ]);
      setPhase({ kind: "loaded", data: { status, readiness, selection, hours, requests, me } });
    } catch (err) {
      const slug = refusalSlug(err);
      setPhase(slug ? { kind: "refused", slug } : { kind: "error", err });
    }
    setSend("idle");
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSend("sending");
    const commandId = attempt.current ?? newCommandId();
    attempt.current = commandId;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PUBLISH_TIMEOUT_MS);
    try {
      await publishProfile(commandId, controller.signal);
      attempt.current = null;
      await load(true);
    } catch (err) {
      const slug = refusalSlug(err);
      if (err instanceof ApiError && err.slug === "not_ready") {
        // Список недостающего — из status, одного источника на все состояния.
        attempt.current = null;
        await load(true);
      } else if (slug) {
        attempt.current = null;
        setPhase({ kind: "refused", slug });
        setSend("idle");
      } else {
        // 5xx, обрыв, таймаут, «ключ уже использован» — исход неизвестен:
        // проверяем состояние, второй POST вслепую не шлём.
        setSend("uncertain");
      }
    } finally {
      clearTimeout(timer);
      inFlight.current = false;
    }
  };

  const checkStatus = async () => {
    setSend("checking");
    await load(true);
  };

  if (phase.kind === "loading") {
    return (
      <main className="screen publication">
        <SystemState kind="loading" lines={2} />
      </main>
    );
  }

  if (phase.kind === "error") {
    return (
      <main className="screen publication">
        <SystemState kind="load_error" what="publication" err={phase.err} onRetry={() => void load()} />
      </main>
    );
  }

  if (phase.kind === "refused") {
    return (
      <main className="screen publication" aria-labelledby="publication-title">
        <h1 id="publication-title" className="publication__title">
          {PUBLICATION_COPY.refusedTitle}
        </h1>
        <p className="publication__lead" data-testid="publication-refusal">
          {PUBLICATION_REFUSAL_TEXT[phase.slug]}
        </p>
        <div className="publication__actions">
          <button type="button" className="btn-primary" onClick={() => void load()}>
            {PUBLICATION_COPY.retry}
          </button>
          <button type="button" className="btn-secondary" onClick={() => navigate(SETUP_ROUTE)}>
            {PUBLICATION_COPY.backToSetup}
          </button>
        </div>
      </main>
    );
  }

  const { data } = phase;
  const { status } = data;
  if (status.profile_status === "active" || status.profile_status === "pending") {
    return (
      <Submitted
        data={data}
        active={status.profile_status === "active"}
        onCabinet={() => navigate(HOME_ROUTE)}
        onAddServices={selfService ? () => navigate(SELECT_PATH) : undefined}
      />
    );
  }

  const ready = status.readiness.status === "READY";
  const missing = groupMissing(status.readiness.missing);
  const identityMissing = missing.get("identity") ?? [];
  const days = workingDayLines(data.hours);
  const summaries: Record<string, string | null> = {
    profile: PUBLICATION_COPY.profileDone,
    services: data.selection
      ? `${data.selection.configured} ${ruPluralServices(data.selection.configured)} с ценой и длительностью`
      : null,
    location: PUBLICATION_COPY.locationDone,
    hours: days.length > 0 ? days.join(", ") : null,
  };
  const review = data.requests.filter((r) => r.status === "pending");

  return (
    <main className="screen publication" aria-labelledby="publication-title">
      <h1 id="publication-title" className="publication__title">
        {ready ? PUBLICATION_COPY.readyTitle : PUBLICATION_COPY.notReadyTitle}
      </h1>
      <p className="publication__lead">{ready ? PUBLICATION_COPY.readyLead : PUBLICATION_COPY.notReadyLead}</p>

      <ul className="publication__list" aria-label={PUBLICATION_COPY.checklistLabel}>
        {SECTIONS.map((section) => (
          <SectionRow
            key={section}
            section={section}
            label={READINESS_ITEM_LABELS[section] ?? section}
            codes={missing.get(section) ?? []}
            summary={summaries[section] ?? null}
            link={sectionLink(data.readiness, section)}
            onOpen={(to) => navigate(to)}
          />
        ))}
        {identityMissing.length > 0 && (
          <SectionRow
            section="identity"
            label={PUBLICATION_COPY.identityLabel}
            codes={identityMissing}
            summary={null}
            link={null}
            onOpen={(to) => navigate(to)}
          />
        )}
      </ul>

      {review.length > 0 && (
        <section className="publication__review" aria-labelledby="publication-review-title">
          <h2 id="publication-review-title" className="publication__section-title">
            {PUBLICATION_COPY.reviewTitle}
          </h2>
          <ul className="publication__services">
            {review.map((r) => (
              <li key={r.id} className="publication__service">
                <span>{`${r.name} · ${formatDuration(r.duration_minutes)} · ${formatMoney(r.price)}`}</span>
                <span className="publication__chip">{r.status_label}</span>
              </li>
            ))}
          </ul>
          <p className="publication__note">{PUBLICATION_COPY.reviewNote}</p>
        </section>
      )}

      {!ready && <p className="publication__note">{PUBLICATION_COPY.notReadyNote}</p>}

      <div className="publication__actions">
        {!ready ? (
          <button type="button" className="btn-primary" onClick={() => navigate(SETUP_ROUTE)}>
            {PUBLICATION_COPY.continueSetup}
          </button>
        ) : send === "uncertain" ? (
          <div className="publication__uncertain" role="status">
            <p className="publication__label">{PUBLICATION_COPY.uncertainTitle}</p>
            <p className="publication__detail">{PUBLICATION_COPY.uncertainLead}</p>
            <button type="button" className="btn-primary" onClick={() => void checkStatus()}>
              {PUBLICATION_COPY.checkStatus}
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn-primary"
            disabled={send !== "idle"}
            aria-busy={send !== "idle"}
            onClick={() => void submit()}
          >
            {send === "idle"
              ? PUBLICATION_COPY.submit
              : send === "checking"
                ? PUBLICATION_COPY.checking
                : PUBLICATION_COPY.sending}
          </button>
        )}
        {ready && (
          <button type="button" className="btn-secondary" onClick={() => navigate(SETUP_ROUTE)}>
            {PUBLICATION_COPY.backToSetup}
          </button>
        )}
      </div>
    </main>
  );
}

function SectionRow({
  section,
  label,
  codes,
  summary,
  link,
  onOpen,
}: {
  section: string;
  label: string;
  codes: PublicationMissingItem[];
  summary: string | null;
  link: string | null;
  onOpen: (to: string) => void;
}) {
  const missing = codes.length > 0;
  const action = PUBLICATION_ACTION_LABEL[section];
  return (
    <li
      className={missing ? "publication__item publication__item--missing" : "publication__item"}
      data-testid={`publication-section-${section}`}
    >
      <span className="publication__mark" aria-hidden="true">
        {missing ? "!" : "✓"}
      </span>
      <div className="publication__body">
        <span className="publication__label">{label}</span>
        {missing
          ? codes.map((item) => (
              <span key={item.code} className="publication__detail publication__detail--missing">
                {PUBLICATION_MISSING_TEXT[item.code] ?? MISSING_FALLBACK}
              </span>
            ))
          : summary && <span className="publication__detail">{summary}</span>}
        {missing && section === "location" && link === null && (
          <span className="publication__detail">{PUBLICATION_COPY.locationUnavailable}</span>
        )}
        {missing && link !== null && action && (
          <button type="button" className="btn-secondary publication__action" onClick={() => onOpen(link)}>
            {action}
          </button>
        )}
      </div>
    </li>
  );
}

function Submitted({
  data,
  active,
  onCabinet,
  onAddServices,
}: {
  data: Loaded;
  active: boolean;
  onCabinet: () => void;
  /** DRF-2254: нет — кнопки «добавить услуги» нет (каталог назвал пространство салоном). */
  onAddServices?: () => void;
}) {
  const days = workingDayLines(data.hours);
  const configured = data.selection?.services.filter((s) => s.configured && s.offer) ?? [];
  const review = data.requests.filter((r) => r.status === "pending");
  const name = data.me?.master.name.trim() ?? "";
  const specialization = data.me?.master.specialization.trim() ?? "";

  return (
    <main className="screen publication" aria-labelledby="publication-title">
      <h1 id="publication-title" className="publication__title">
        {active ? PUBLICATION_COPY.activeTitle : PUBLICATION_COPY.pendingTitle}
      </h1>
      <p className="publication__lead">{active ? PUBLICATION_COPY.activeLead : PUBLICATION_COPY.pendingLead}</p>

      <div className="publication__summary">
        {name && <p className="publication__name">{name}</p>}
        {specialization && <p className="publication__detail">{specialization}</p>}
        {data.selection && (
          <p className="publication__detail">
            {`${data.selection.configured} ${ruPluralServices(data.selection.configured)}`}
          </p>
        )}
        {days.length > 0 && (
          <ul className="publication__days" aria-label={PUBLICATION_COPY.daysLabel}>
            {days.map((line) => (
              <li key={line} className="publication__day">
                {line}
              </li>
            ))}
          </ul>
        )}
      </div>

      {configured.length > 0 && (
        <section className="publication__review" aria-labelledby="publication-services-title">
          <h2 id="publication-services-title" className="publication__section-title">
            {PUBLICATION_COPY.servicesTitle}
          </h2>
          <ul className="publication__services">
            {configured.map((s) => (
              <li key={s.salon_service_id} className="publication__service">
                <span>
                  {`${s.name} · ${formatDuration(s.offer?.duration_minutes ?? null)} · ${formatMoney(s.offer?.price ?? null)}`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {review.length > 0 && <p className="publication__note">{PUBLICATION_COPY.reviewAfter}</p>}

      <div className="publication__actions">
        <button type="button" className="btn-primary" onClick={onCabinet}>
          {PUBLICATION_COPY.toCabinet}
        </button>
        {onAddServices ? (
          <button type="button" className="btn-secondary" onClick={onAddServices}>
            {PUBLICATION_COPY.addServices}
          </button>
        ) : null}
      </div>
    </main>
  );
}
