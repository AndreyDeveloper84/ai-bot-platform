/**
 * Master M0 onboarding — 3-step flow + state-matrix screens.
 *
 * Route: /onboarding/master?token=<uuid>
 *
 * Spec source: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M0
 * (lines 167–276). All Russian copy is verbatim from the spec — DO NOT
 * paraphrase. The 3 steps:
 *
 *   Step 1 — Welcome + identity confirm   (POST /onboarding/claim, no mutation)
 *   Step 2 — Permissions explainer        (radical transparency)
 *   Step 3 — Profile starter              (POST /onboarding/accept + PATCH /onboarding/profile)
 *
 * Error sub-screens (§M0 lines 261–264 + customer-first-touch state-matrix
 * discipline):
 *
 *   - token invalid / expired (404 / 410)
 *   - token already used (409)            → routes to /master/dashboard
 *   - wrong recipient (403)
 *   - network / 5xx
 *   - "Это не я" → calls reject → wrong-recipient sub-screen
 *
 * Bridge API used (§M0 lines 266–271):
 *   - initData via Authorization: MaxInitData
 *   - WebApp.BackButton.show() from Step 2 onward (hidden on Step 1 + errors)
 *   - WebApp.HapticFeedback.notificationOccurred('success') on Step 3 done
 *   - WebApp.enableClosingConfirmation() on Step 3 only if bio or photo dirty
 *   - WebApp.DeviceStorage.setItem('master_token', ...) after accept
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../lib/api";
import { salonOwnerHint } from "../lib/salonOwnerHint";
import {
  acceptInvite,
  claimInvite,
  patchOnboardingProfile,
  rejectInvite,
  MASTER_SESSION_STORAGE_KEY,
  type ClaimResponse,
} from "../lib/master-api";
import {
  closeApp,
  hapticNotify,
  setDeviceStorage,
  signalReady,
} from "../lib/max-sdk";
import { ScreenLayout } from "../components/ScreenLayout";
import { useReloadMe } from "../state/boot";
import { StickyCta } from "../components/StickyCta";
import { backByAction, screenRoot, type BackIntent } from "../lib/screen-back";
import { useClosingConfirmation } from "../hooks/useClosingConfirmation";

const BIO_MAX = 280;

type Step = 1 | 2 | 3;

type FlowState =
  | { kind: "loading" }
  | { kind: "missing_token" }
  | { kind: "ready"; data: ClaimResponse; step: Step }
  | { kind: "submitting"; data: ClaimResponse }
  | { kind: "error_invalid" }
  | { kind: "error_used" }
  | { kind: "error_wrong_recipient" }
  | { kind: "error_network"; err: unknown };

interface ProfileDraft {
  bio: string;
  photo: File | null;
  /** Local preview URL — set after the user picks a file. Revoked on unmount. */
  photoPreview: string | null;
}

const EMPTY_DRAFT: ProfileDraft = { bio: "", photo: null, photoPreview: null };

// --- Russian copy (VERBATIM from §M0) -------------------------------------

const COPY = {
  loading: "Загружаем ваш профиль…",
  step1: {
    greeting: (firstName: string) => `Здравствуйте, ${firstName}!`,
    body: (salonName: string) =>
      `${salonName} пригласила вас в помощник студии. Здесь вы будете видеть своё расписание и общаться с клиентами.`,
    question: "Это вы?",
    // DRF-1434 — карточка ниже это ЗАПИСЬ САЛОНА, а не профиль MAX.
    // Без подписи её имя выглядело вторым именем того же человека.
    recordCaption: "Салон записал вас так:",
    nameMismatch: (maxName: string) =>
      `В MAX вы «${maxName}» — имя в приглашении другое. Если оно неверное, продолжайте: студия поправит его в вашем профиле.`,
    cta: "Это я, продолжить",
    notMe: "Это не я",
  },
  step2: {
    seeTitle: "Что вы увидите",
    seeItems: [
      "Свой график на день и неделю",
      "Сообщения от своих клиентов",
      "Имя клиента и историю визитов",
    ],
    notSeeTitle: "Что вы НЕ увидите",
    notSeeItems: [
      "Номера телефонов клиентов",
      "Финансовые данные",
      "Чужие диалоги и других мастеров",
    ],
    // `ownerHint` is an arbitrary tenant string (salon name or the neutral
    // default), so no gendered pronoun can be derived from it — ask «у студии».
    footer: (ownerHint: string) =>
      `Это сделано, чтобы защитить данные клиентов. ${ownerHint} может при необходимости расширить доступ — спросите у студии.`,
    cta: "Понятно",
  },
  step3: {
    title: "Заполните профиль",
    subtitle: "(можно потом)",
    photoLabel: "Фото",
    upload: "Загрузить",
    bioLabel: "О себе (для клиентов)",
    bioPlaceholder: "Опишите свой опыт...",
    bioMaxHint: "Максимум 280 символов",
    servicesLabel: "Услуги",
    servicesIntro: (services: string) =>
      `Сейчас Карина указала: ${services}.`,
    servicesFooter: "Если что-то не так — напишите ей.",
    cta: "Сохранить и продолжить",
    later: "Заполнить позже",
  },
  errors: {
    missing_token: "Откройте ссылку из приглашения от вашей студии.",
    invalid:
      "Ссылка устарела. Попросите Карину прислать новую.",
    copyDeeplink: "Скопировать ссылку на бота",
    used: "Вы уже подключены — открыть рабочий стол",
    wrong_recipient:
      "Сообщите Карине — возможно, ссылку отправили не туда.",
    close: "Закрыть",
    network: "Связь пропала. Попробуйте ещё раз.",
    retry: "Попробовать снова",
  },
};

// --- Helpers --------------------------------------------------------------

function tokenFromSearch(s: URLSearchParams): string {
  return (s.get("token") || "").trim();
}

function classifyClaimError(err: unknown): FlowState {
  if (err instanceof ApiError) {
    if (err.slug === "invalid_invite_token" || err.slug === "invite_expired") {
      return { kind: "error_invalid" };
    }
    if (err.slug === "invite_already_used") {
      return { kind: "error_used" };
    }
    if (err.slug === "wrong_recipient") {
      return { kind: "error_wrong_recipient" };
    }
  }
  return { kind: "error_network", err };
}

// --- Component ------------------------------------------------------------

export function MasterOnboardingScreen() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = tokenFromSearch(params);

  const [state, setState] = useState<FlowState>(
    token ? { kind: "loading" } : { kind: "missing_token" },
  );
  const [draft, setDraft] = useState<ProfileDraft>(EMPTY_DRAFT);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Step 3 dirty bit drives closing-confirmation.
  const step3Dirty =
    state.kind === "ready" &&
    state.step === 3 &&
    (draft.bio.trim().length > 0 || draft.photo !== null);

  // BackButton wiring:
  //   Step 1 + errors + loading → hidden
  //   Step 2 → onBack: setStep(1)
  //   Step 3 → onBack: setStep(2)
  const onBack = useMemo(() => {
    if (state.kind !== "ready") return undefined;
    if (state.step === 2)
      return () => setState({ kind: "ready", data: state.data, step: 1 });
    if (state.step === 3)
      return () => setState({ kind: "ready", data: state.data, step: 2 });
    return undefined;
  }, [state]);
  // Вид экрана (DRF-1493). Возврат здесь никуда не уводит с адреса —
  // это шаг назад внутри мастера. На первом шаге и на всех экранах
  // ошибок возвращаться некуда: приглашение открывают по ссылке из
  // бота, истории за ним нет.
  const back: BackIntent = onBack
    ? backByAction(onBack)
    : screenRoot(
        "Первый шаг мастера онбординга и его экраны ошибок — вход по " +
          "ссылке-приглашению из бота; предыдущего экрана не существует.",
      );
  useClosingConfirmation(step3Dirty);

  // Cleanup the local photo-preview blob URL.
  useEffect(() => {
    return () => {
      if (draft.photoPreview) URL.revokeObjectURL(draft.photoPreview);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Initial claim call.
  const runClaim = useCallback(() => {
    if (!token) {
      setState({ kind: "missing_token" });
      return;
    }
    setState({ kind: "loading" });
    signalReady();
    let cancelled = false;
    claimInvite(token)
      .then((data) => {
        if (!cancelled) setState({ kind: "ready", data, step: 1 });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState(classifyClaimError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    const cancel = runClaim();
    return cancel;
  }, [runClaim]);

  // --- Step 1 handlers ----------------------------------------------------

  const handleConfirmIdentity = useCallback(() => {
    if (state.kind !== "ready") return;
    setState({ kind: "ready", data: state.data, step: 2 });
  }, [state]);

  const handleNotMe = useCallback(async () => {
    if (state.kind !== "ready") return;
    // Best-effort reject — even if the server returns 409 (already used)
    // the user-facing outcome is the same: «не вы».
    try {
      await rejectInvite(token);
    } catch {
      /* swallow — UI lands on wrong-recipient regardless */
    }
    setState({ kind: "error_wrong_recipient" });
  }, [state, token]);

  // --- Step 2 → Step 3 ----------------------------------------------------

  const handleStep2Continue = useCallback(() => {
    if (state.kind !== "ready") return;
    setState({ kind: "ready", data: state.data, step: 3 });
  }, [state]);

  // --- Step 3: photo + submit ---------------------------------------------

  const handlePhotoPick = useCallback((file: File | null) => {
    setDraft((prev) => {
      if (prev.photoPreview) URL.revokeObjectURL(prev.photoPreview);
      return {
        ...prev,
        photo: file,
        photoPreview: file ? URL.createObjectURL(file) : null,
      };
    });
  }, []);

  const onBioChange = useCallback((next: string) => {
    // Clip at the wire-format limit. Spec line 247: «Максимум 280 символов».
    setDraft((prev) => ({ ...prev, bio: next.slice(0, BIO_MAX) }));
  }, []);

  /**
   * DRF-1434 — принять приглашение мало, надо ещё сказать оболочке, что
   * роль изменилась.
   *
   * `POST /onboarding/accept` ставит `linked_bot_user` и
   * `invite_status = ACCEPTED` (apps/master_api/views.py), после чего
   * `resolve_role` отдаёт `is_master: true`. Но `App` прочитал
   * `/api/v1/me` один раз на старте — тогда роли ещё не было, — и
   * держит `CustomerRoutes`. `/master/dashboard` там не объявлен, и до
   * этой правки его доедал `<Route path="*" element={<HelloScreen />} />`:
   * человек, прошедший весь путь мастера, получал клиентское
   * «Помогу записаться в студию» внутри бота для мастеров.
   *
   * Порядок важен. `navigate` идёт первым — синхронно, пока экран ещё
   * смонтирован. `reloadMe()` следом переводит `App` в `loading`, что
   * размонтирует всё поддерево: если бы он шёл первым, `navigate`
   * вызывался бы уже из размонтированного компонента, а этот экран
   * успел бы перемонтироваться в дереве мастера и заново дёрнуть
   * `claimInvite`.
   */
  const reloadMe = useReloadMe();
  const finishAndRouteToDashboard = useCallback(() => {
    hapticNotify("success");
    navigate("/master/dashboard", { replace: true });
    reloadMe();
  }, [navigate, reloadMe]);

  const handleStep3Submit = useCallback(
    async (saveProfile: boolean) => {
      if (state.kind !== "ready") return;
      setState({ kind: "submitting", data: state.data });
      try {
        const accepted = await acceptInvite(token);
        setDeviceStorage(MASTER_SESSION_STORAGE_KEY, accepted.session_token);

        if (saveProfile && (draft.bio.trim() || draft.photo)) {
          try {
            await patchOnboardingProfile({
              bio: draft.bio.trim(),
              photo: draft.photo,
            });
          } catch (err) {
            // Profile patch failure isn't fatal — user is already accepted.
            // Log + continue; they can edit later in M4.
            console.warn("[master/m0] profile patch failed", err);
          }
        }
        finishAndRouteToDashboard();
      } catch (err: unknown) {
        if (err instanceof ApiError && err.slug === "invite_already_used") {
          setState({ kind: "error_used" });
          return;
        }
        if (err instanceof ApiError && err.slug === "wrong_recipient") {
          setState({ kind: "error_wrong_recipient" });
          return;
        }
        setState({ kind: "error_network", err });
      }
    },
    [draft, state, token, finishAndRouteToDashboard],
  );

  // --- Render -------------------------------------------------------------

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={back}>
        <p>{COPY.loading}</p>
      </ScreenLayout>
    );
  }

  if (state.kind === "missing_token") {
    return (
      <ScreenLayout back={back} title="Не получилось войти">
        <p>{COPY.errors.missing_token}</p>
      </ScreenLayout>
    );
  }

  if (state.kind === "error_invalid") {
    return <InviteInvalidScreen />;
  }
  if (state.kind === "error_used") {
    return <InviteUsedScreen onOpen={() => navigate("/master/dashboard")} />;
  }
  if (state.kind === "error_wrong_recipient") {
    return <WrongRecipientScreen />;
  }
  if (state.kind === "error_network") {
    return <NetworkErrorScreen onRetry={runClaim} err={state.err} />;
  }

  // --- 3-step flow --------------------------------------------------------

  const data = state.kind === "ready" ? state.data : state.data;
  const submitting = state.kind === "submitting";
  const step = state.kind === "ready" ? state.step : 3;

  if (step === 1) {
    return (
      <Step1Identity
        back={back}
        data={data}
        onConfirm={handleConfirmIdentity}
        onNotMe={handleNotMe}
        disabled={submitting}
      />
    );
  }
  if (step === 2) {
    return (
      <Step2Permissions back={back} data={data} onContinue={handleStep2Continue} />
    );
  }
  return (
    <Step3Profile
      back={back}
      data={data}
      draft={draft}
      onBioChange={onBioChange}
      onPhotoPick={handlePhotoPick}
      fileInputRef={fileInputRef}
      onSubmit={() => handleStep3Submit(true)}
      onLater={() => handleStep3Submit(false)}
      submitting={submitting}
    />
  );
}

// --- Step 1 ---------------------------------------------------------------

function Step1Identity({
  back,
  data,
  onConfirm,
  onNotMe,
  disabled,
}: {
  data: ClaimResponse;
  onConfirm: () => void;
  onNotMe: () => void;
  disabled: boolean;
  back: BackIntent;
}) {
  // DRF-1434 — на пилоте этот экран показывал два разных имени: в
  // заголовке «Здравствуйте, Иван!» (из профиля MAX), а в карточке под
  // вопросом «Это вы?» — «Я тест» (из записи приглашения, которую завёл
  // салон). Человека просили подтвердить личность, показывая ему две.
  //
  // Оставлены оба источника, но каждый теперь подписан. Заголовок —
  // это ЧЕЛОВЕК: MAX проверил его сессию, `first_name` оттуда, и
  // здороваться чужим именем из чужой таблицы неправильно. Карточка —
  // это ЗАПИСЬ САЛОНА, ровно то, что предлагается подтвердить; она и
  // подписана как запись салона. Когда имена расходятся, экран говорит
  // об этом вслух и подсказывает, что делать, — раньше расхождение
  // выглядело как ошибка приложения.
  const maxFirstName = data.max_user.first_name.trim();
  const masterName = data.master.name.trim();
  const greetingName =
    maxFirstName || masterName.split(/\s+/)[0] || "коллега";
  const nameMismatch =
    maxFirstName !== "" &&
    masterName !== "" &&
    masterName.toLocaleLowerCase("ru") !==
      maxFirstName.toLocaleLowerCase("ru") &&
    masterName.split(/\s+/)[0]?.toLocaleLowerCase("ru") !==
      maxFirstName.toLocaleLowerCase("ru");
  const salon = data.salon.name;
  const phone = data.max_user.phone_masked;
  const handle = data.max_user.max_handle
    ? `@${data.max_user.max_handle.replace(/^@/, "")}`
    : "";
  return (
    <ScreenLayout
      back={back}
      title={COPY.step1.greeting(greetingName)}
      cta={
        <StickyCta onClick={onConfirm} disabled={disabled}>
          {COPY.step1.cta}
        </StickyCta>
      }
    >
      <p style={{ marginTop: 0 }}>{COPY.step1.body(salon)}</p>
      <p>
        <strong>{COPY.step1.question}</strong>
      </p>
      <p className="identity-caption" id="m0-identity-caption">
        {COPY.step1.recordCaption}
      </p>
      <div className="profile-identity" aria-labelledby="m0-identity-caption">
        <div style={{ fontWeight: 600, fontSize: "var(--font-size-300)" }}>
          {masterName}
        </div>
        {phone ? <div className="profile-phone">{phone}</div> : null}
        {handle ? <div className="profile-phone">MAX: {handle}</div> : null}
        {nameMismatch ? (
          <p className="identity-mismatch">
            {COPY.step1.nameMismatch(maxFirstName)}
          </p>
        ) : null}
      </div>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button
          type="button"
          className="btn-secondary"
          onClick={onNotMe}
          disabled={disabled}
        >
          {COPY.step1.notMe}
        </button>
      </div>
    </ScreenLayout>
  );
}

// --- Step 2 ---------------------------------------------------------------

function Step2Permissions({
  back,
  data,
  onContinue,
}: {
  data: ClaimResponse;
  onContinue: () => void;
  back: BackIntent;
}) {
  // Spec line 219-221 references "Карина" by name. That is a spec example, not
  // data: the backend surfaces no owner first name, only `salon.name`. Step 1
  // already renders the salon name, so step 2 uses the same source rather than
  // a literal that is wrong for every tenant. See lib/salonOwnerHint.ts.
  const ownerHint = salonOwnerHint(data.salon.name);
  return (
    <ScreenLayout
      back={back}
      cta={<StickyCta onClick={onContinue}>{COPY.step2.cta}</StickyCta>}
    >
      <h1 className="screen__title">{COPY.step2.seeTitle}</h1>
      <ul style={{ paddingInlineStart: "1.2em", margin: "var(--s-2) 0" }}>
        {COPY.step2.seeItems.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <h2
        className="screen__title"
        style={{ fontSize: "var(--font-size-400)", marginTop: "var(--s-4)" }}
      >
        {COPY.step2.notSeeTitle}
      </h2>
      <ul style={{ paddingInlineStart: "1.2em", margin: "var(--s-2) 0" }}>
        {COPY.step2.notSeeItems.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p style={{ color: "var(--c-text-secondary)" }}>
        {COPY.step2.footer(ownerHint)}
      </p>
    </ScreenLayout>
  );
}

// --- Step 3 ---------------------------------------------------------------

function Step3Profile({
  back,
  data,
  draft,
  onBioChange,
  onPhotoPick,
  fileInputRef,
  onSubmit,
  onLater,
  submitting,
}: {
  data: ClaimResponse;
  draft: ProfileDraft;
  onBioChange: (next: string) => void;
  onPhotoPick: (file: File | null) => void;
  fileInputRef: React.MutableRefObject<HTMLInputElement | null>;
  onSubmit: () => void;
  onLater: () => void;
  submitting: boolean;
  back: BackIntent;
}) {
  const services = data.master.services
    .map((s) => s.name)
    .join(", ");
  // Avatar fallback: master initials.
  const initials = data.master.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
  return (
    <ScreenLayout
      back={back}
      cta={
        <StickyCta onClick={onSubmit} disabled={submitting}>
          {COPY.step3.cta}
        </StickyCta>
      }
    >
      <h1 className="screen__title">
        {COPY.step3.title}{" "}
        <small
          style={{
            fontWeight: 400,
            fontSize: "var(--font-size-200)",
            color: "var(--c-text-secondary)",
          }}
        >
          {COPY.step3.subtitle}
        </small>
      </h1>

      <div className="profile-section" aria-label={COPY.step3.photoLabel}>
        <div className="profile-section__title">{COPY.step3.photoLabel}</div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
          <div className="master-card__avatar" style={{ width: 64, height: 64 }}>
            {draft.photoPreview ? (
              <img src={draft.photoPreview} alt="Превью фото" />
            ) : (
              <span>{initials || "—"}</span>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              onPhotoPick(f);
            }}
          />
          <button
            type="button"
            className="btn-secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={submitting}
          >
            {COPY.step3.upload}
          </button>
        </div>
      </div>

      <div className="profile-section">
        <label className="profile-field">
          <span className="profile-field__label">{COPY.step3.bioLabel}</span>
          <textarea
            className="profile-input profile-input--multiline"
            placeholder={COPY.step3.bioPlaceholder}
            value={draft.bio}
            maxLength={BIO_MAX}
            onChange={(e) => onBioChange(e.target.value)}
            disabled={submitting}
            rows={4}
          />
          <span className="profile-field__hint" aria-live="polite">
            {COPY.step3.bioMaxHint} · {draft.bio.length}/{BIO_MAX}
          </span>
        </label>
      </div>

      <div className="profile-section">
        <div className="profile-section__title">{COPY.step3.servicesLabel}</div>
        {services ? (
          <p style={{ margin: 0 }}>
            {COPY.step3.servicesIntro(services)} {COPY.step3.servicesFooter}
          </p>
        ) : (
          <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>
            Услуги ещё не назначены. {COPY.step3.servicesFooter}
          </p>
        )}
      </div>

      <div style={{ marginTop: "var(--s-3)" }}>
        <button
          type="button"
          className="btn-secondary"
          onClick={onLater}
          disabled={submitting}
        >
          {COPY.step3.later}
        </button>
      </div>
    </ScreenLayout>
  );
}

// --- Error sub-screens ----------------------------------------------------

/**
 * Вид экранов ошибок приглашения (DRF-1493): вход по ссылке из бота,
 * истории за ним нет — возвращаться некуда.
 */
const INVITE_ERROR_BACK = screenRoot(
  "Экран ошибки приглашения открыт по ссылке из бота напрямую.",
);

function InviteInvalidScreen() {
  return (
    <ScreenLayout back={INVITE_ERROR_BACK} title="Ссылка не работает">
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.errors.invalid}</p>
      </div>
    </ScreenLayout>
  );
}

function InviteUsedScreen({ onOpen }: { onOpen: () => void }) {
  return (
    <ScreenLayout
      back={INVITE_ERROR_BACK}
      title="Уже подключены"
      cta={<StickyCta onClick={onOpen}>{COPY.errors.used}</StickyCta>}
    >
      <p>Этот аккаунт уже завершил онбординг. Откроем рабочий стол.</p>
    </ScreenLayout>
  );
}

function WrongRecipientScreen() {
  return (
    <ScreenLayout
      back={INVITE_ERROR_BACK}
      title="Не тот получатель"
      cta={<StickyCta onClick={closeApp}>{COPY.errors.close}</StickyCta>}
    >
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.errors.wrong_recipient}</p>
      </div>
    </ScreenLayout>
  );
}

function NetworkErrorScreen({
  onRetry,
  err,
}: {
  onRetry: () => void;
  err: unknown;
}) {
  // Surface 5xx vs offline differently per spec §M0 + customer-first-touch.
  const isServer = err instanceof ApiError && err.status >= 500;
  return (
    <ScreenLayout
      back={INVITE_ERROR_BACK}
      title={isServer ? "Сервис временно недоступен" : "Нет связи"}
    >
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.errors.network}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {COPY.errors.retry}
          </button>
        </div>
      </div>
    </ScreenLayout>
  );
}
