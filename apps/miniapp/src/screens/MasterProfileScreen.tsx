/**
 * Master M4 profile editor — full screen.
 *
 * Route: /master/profile (replaces MasterProfilePlaceholderScreen).
 *
 * Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md §M4
 * (lines 480-553). Verbatim Russian copy throughout.
 *
 * Spec quote (§M4 lines 487-523, layout block):
 *
 *     «← Профиль … Как видят клиенты … [АП] Анна Петрова / Мастер по
 *     ногтям / «5 лет опыта, люблю когда красиво» … [Изменить фото] /
 *     [Изменить «О себе»] … ━━ УСЛУГИ ━━ … Хотите добавить или убрать?
 *     [Написать Карине ›] … ━━ РАБОЧЕЕ ВРЕМЯ ━━ … Пн—Сб: 10:00–19:00 …
 *     [Запросить изменение ›] … ━━ ОТЗЫВЫ КЛИЕНТОВ ━━ … За последние
 *     30 дней: …»
 *
 * Spec quote (§M4 lines 549-552, Bridge API):
 *
 *     «WebApp.SecureStorage … WebApp.enableClosingConfirmation() when
 *     bio/photo dirty … HapticFeedback.notificationOccurred('success')
 *     on save»
 *
 * IN scope (this screen):
 *   - Section 1: profile (avatar, name read-only, specialization read-only,
 *     bio inline-edit, photo upload)
 *   - Section 2: services (read-only list + «Написать Карине ›» CTA —
 *     deferred to disabled-tooltip since master-side internal-chat UI
 *     isn't wired yet; backend exists in apps/internal_chat/)
 *   - Section 3: working hours (read-only summary if available + «Запросить
 *     изменение ›» → /master/schedule, which exposes the existing M3
 *     availability-request flow)
 *   - Section 4: reviews stub (disabled — reviews backend not built)
 *   - State matrix: loading skeleton, save spinner, save error, photo
 *     errors (size / MIME / network), pending owner-approval chip
 *     pattern (wired for display-name field even though the field is
 *     disabled this PR — reuse pattern for future fields), offline
 *     banner.
 *
 * OUT of scope (deferred, documented in PR body):
 *   - Display-name change (needs owner-approval flow; pattern shows
 *     disabled tooltip)
 *   - Reviews backend
 *   - Services add/remove (master proposes → owner approves) — pointer
 *     stays at «Написать Карине» CTA
 *   - Quiet hours / theme / push notification settings → M7
 *
 * Backend reuse decision: Option B (URL alias). The PATCH endpoint is
 * the existing apps/master_api/views.py::onboarding_profile wired via
 * a new ``path("profile", ...)`` alias in apps/master_api/urls.py. Same
 * view function — idempotent + last-write-wins per the view docstring.
 * Audit event slug still reads ``MASTER_PROFILE_INITIALIZED``; a
 * follow-up backend cleanup ticket can add ``MASTER_PROFILE_UPDATED``.
 *
 * Photo upload bypass note: ``uploadMasterProfilePhoto`` skips the
 * shared ``request()`` helper and calls ``fetch`` directly with
 * ``applyDevBypassHeaders``. The reason is the MM3/MM4 lesson:
 * ``request()`` auto-sets ``Content-Type: application/json`` whenever a
 * body is present, which clobbers the browser's multipart boundary
 * string and the backend MultiPartParser then rejects with 400.
 *
 * Bridge API:
 *   - BackButton.show() → /master/dashboard
 *   - enableClosingConfirmation() while any inline editor is dirty OR
 *     while a photo upload is in flight
 *   - hapticSelection() on edit-open
 *   - hapticNotify('success') / hapticNotify('error') on save outcomes
 *   - Plain ``<input type=file>`` for photo upload (§M4 line 550)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { MasterTabBar } from "../components/MasterTabBar";
import { Snackbar } from "../components/Snackbar";
import { ApiError } from "../lib/api";
import {
  MASTER_PROFILE_BIO_MAX,
  MASTER_PROFILE_PHOTO_MAX_BYTES,
  MASTER_PROFILE_PHOTO_MIME_ALLOWLIST,
  getMasterMe,
  patchMasterProfile,
  uploadMasterProfilePhoto,
  type MasterMeMaster,
  type MasterMeResponse,
} from "../lib/master-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../lib/max-sdk";

// --- Russian copy (VERBATIM from §M4) -----------------------------------

const COPY = {
  header: "Профиль",
  sections: {
    asClientsSeeYou: "Как видят клиенты",
    services: "━━ УСЛУГИ ━━━━━━━━━━━━━━━━━━",
    workHours: "━━ РАБОЧЕЕ ВРЕМЯ ━━━━━━━━━━━━",
    reviews: "━━ ОТЗЫВЫ КЛИЕНТОВ ━━━━━━━━━━━",
    settings: "━━ НАСТРОЙКИ ━━━━━━━━━━━━━━━",
  },
  buttons: {
    editPhoto: "Изменить фото",
    editBio: "Изменить «О себе»",
    writeToOwner: "Написать Карине ›",
    requestScheduleChange: "Запросить изменение ›",
    notificationSettings: "Настройки уведомлений ›",
    internalChat: "Со студией ›",
    appSettings: "Настройки приложения ›",
    save: "Сохранить",
    cancel: "Отмена",
    retry: "Попробовать снова",
  },
  services: {
    helper: "Хотите добавить или убрать?",
    empty: "Услуги ещё не назначены.",
  },
  workHours: {
    fallback:
      "Информацию о рабочем времени уточните у администратора.",
  },
  reviews: {
    placeholder: "За последние 30 дней: — отзывов. Скоро.",
  },
  bioEdit: {
    title: "О себе",
    placeholder: "Расскажите о себе в нескольких словах…",
  },
  pendingApproval: "На рассмотрении у Карины",
  displayNameTooltip:
    "Изменение имени требует подтверждения от Карины — скоро.",
  reviewsTooltip:
    "Отзывы появятся позже — мы готовим этот раздел.",
  internalChatHint: "Личный канал общения с админами студии.",
  toasts: {
    saved: "✓ Сохранено",
    photoSaved: "✓ Фото обновлено",
  },
  states: {
    loading: "Загружаем профиль…",
    errorTitle: "Не получилось загрузить",
    errorBody:
      "Не получилось загрузить ваш профиль. Проверьте интернет и попробуйте снова.",
    saveError: "Не удалось сохранить. Попробуйте ещё раз.",
    photoTooLarge: "Фото больше 10 МБ. Уменьшите размер.",
    photoBadMime: "Поддерживаются JPG / PNG / WebP",
    photoNetwork:
      "Не получилось загрузить фото. Проверьте интернет и попробуйте снова.",
    bioTooLong: `Длиннее ${MASTER_PROFILE_BIO_MAX} символов нельзя.`,
    offlineBanner: "Нет связи. Сохраним, как только сеть появится.",
  },
};

// --- Helpers ------------------------------------------------------------

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("");
}

/**
 * Format service duration as «N мин». Returns empty string when the
 * backend omits it (Phase 0 catalog mirror occasionally lacks the
 * field for newly imported services).
 */
function formatDuration(min: number | null): string {
  if (typeof min !== "number" || !Number.isFinite(min) || min <= 0) return "";
  return `${Math.round(min)} мин`;
}

// --- State model --------------------------------------------------------

interface BioEditorState {
  value: string;
  saving: boolean;
  err: string;
}

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: MasterMeResponse }
  | { kind: "error"; err: unknown };

// --- Component ----------------------------------------------------------

export function MasterProfileScreen() {
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [editor, setEditor] = useState<BioEditorState | null>(null);
  const [photoUploading, setPhotoUploading] = useState(false);
  const [photoErr, setPhotoErr] = useState("");
  const [offlineBanner, setOfflineBanner] = useState(false);
  const [toast, setToast] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // --- Bridge: BackButton + closing-confirmation ---
  const originalBio =
    phase.kind === "ready" ? phase.data.master.bio : "";
  const isBioDirty =
    editor !== null && (editor.value ?? "") !== (originalBio ?? "");

  useEffect(() => {
    setBackButton(true);
    const off = onBackButton(() => {
      hapticSelection();
      navigate("/master/dashboard");
    });
    return () => {
      off();
      setBackButton(false);
      setClosingConfirmation(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setClosingConfirmation(isBioDirty || photoUploading);
  }, [isBioDirty, photoUploading]);

  // --- Initial load ---
  const fetchMe = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const data = await getMasterMe();
      setPhase({ kind: "ready", data });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void fetchMe();
  }, [fetchMe]);

  // --- Bio editor lifecycle ---
  const openBioEditor = useCallback(() => {
    if (phase.kind !== "ready") return;
    hapticSelection();
    setEditor({
      value: phase.data.master.bio ?? "",
      saving: false,
      err: "",
    });
  }, [phase]);

  const cancelBioEditor = useCallback(() => {
    setEditor(null);
  }, []);

  const updateBio = useCallback((value: string) => {
    setEditor((curr) =>
      curr ? { ...curr, value, err: "" } : curr,
    );
  }, []);

  const saveBio = useCallback(async () => {
    if (!editor || phase.kind !== "ready") return;
    const trimmed = editor.value;
    if (trimmed.length > MASTER_PROFILE_BIO_MAX) {
      setEditor({ ...editor, err: COPY.states.bioTooLong });
      hapticNotify("error");
      return;
    }
    setEditor({ ...editor, saving: true, err: "" });
    setOfflineBanner(false);
    try {
      const res = await patchMasterProfile({ bio: trimmed });
      hapticNotify("success");
      // Merge the patched fields back into the cached me payload — the
      // PATCH response only carries {id, name, bio, photo_url}.
      setPhase({
        kind: "ready",
        data: {
          ...phase.data,
          master: {
            ...phase.data.master,
            bio: res.master.bio,
            photo_url: res.master.photo_url,
          },
        },
      });
      setEditor(null);
      setToast(COPY.toasts.saved);
    } catch (e) {
      if (e instanceof ApiError) {
        setEditor({
          ...editor,
          saving: false,
          err: e.detail || COPY.states.saveError,
        });
      } else {
        setOfflineBanner(true);
        setEditor({ ...editor, saving: false, err: "" });
      }
      hapticNotify("error");
    }
  }, [editor, phase]);

  // --- Photo upload ---
  const handlePhotoSelect = useCallback(
    async (file: File) => {
      if (phase.kind !== "ready") return;
      setPhotoErr("");
      if (file.size > MASTER_PROFILE_PHOTO_MAX_BYTES) {
        setPhotoErr(COPY.states.photoTooLarge);
        hapticNotify("error");
        return;
      }
      const mime = (file.type || "").toLowerCase();
      if (!MASTER_PROFILE_PHOTO_MIME_ALLOWLIST.has(mime)) {
        setPhotoErr(COPY.states.photoBadMime);
        hapticNotify("error");
        return;
      }
      setPhotoUploading(true);
      try {
        const res = await uploadMasterProfilePhoto(file);
        hapticNotify("success");
        setPhase({
          kind: "ready",
          data: {
            ...phase.data,
            master: {
              ...phase.data.master,
              photo_url: res.master.photo_url,
              bio: res.master.bio,
            },
          },
        });
        setToast(COPY.toasts.photoSaved);
      } catch (e) {
        if (e instanceof ApiError) {
          setPhotoErr(e.detail || COPY.states.photoNetwork);
        } else {
          setPhotoErr(COPY.states.photoNetwork);
        }
        hapticNotify("error");
      } finally {
        setPhotoUploading(false);
        // Allow re-selecting the same file (browsers de-dupe by name).
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [phase],
  );

  const openFilePicker = useCallback(() => {
    if (photoUploading) return;
    hapticSelection();
    fileInputRef.current?.click();
  }, [photoUploading]);

  // --- Navigation handlers ---
  const goToScheduleRequest = useCallback(() => {
    hapticSelection();
    // The existing M3 schedule screen exposes the «Запросить выходной» /
    // «Запросить изменение» flow via the per-day CTA. Sending the master
    // there is the documented bridge to availability requests.
    navigate("/master/schedule");
  }, [navigate]);

  // M7 entry point — spec §M7 line 780 «Profile → «Уведомления»».
  const goToNotificationSettings = useCallback(() => {
    hapticSelection();
    navigate("/master/settings/notifications");
  }, [navigate]);

  // M8 minimal entry point — logout-only app settings screen.
  // Full M8 (theme/font/help/about/version) deferred post-pilot.
  const goToAppSettings = useCallback(() => {
    hapticSelection();
    navigate("/master/settings");
  }, [navigate]);

  // Internal-chat entry — opens new thread tagged ``general`` so the
  // M4 «Написать Карине ›» CTA lands directly on the compose flow
  // (handoff §3.3 + §5.3 — generic CTA defaults to `general` topic).
  const goToWriteOwner = useCallback(() => {
    hapticSelection();
    navigate("/master/internal-chat?new=1&topic=general");
  }, [navigate]);

  // «Со студией ›» — opens the list view (master's archive of threads
  // with the admin team).
  const goToInternalChatList = useCallback(() => {
    hapticSelection();
    navigate("/master/internal-chat");
  }, [navigate]);

  // --- Render branches ---
  if (phase.kind === "loading") {
    return (
      <ProfileFrame>
        <LoadingSkeleton />
      </ProfileFrame>
    );
  }
  if (phase.kind === "error") {
    return (
      <ProfileFrame>
        <ErrorBanner err={phase.err} onRetry={() => void fetchMe()} />
      </ProfileFrame>
    );
  }

  const { master } = phase.data;

  return (
    <ProfileFrame>
      {offlineBanner ? <OfflineBanner /> : null}

      <ProfileSection title={COPY.sections.asClientsSeeYou}>
        <ProfileHeaderRow master={master} />

        <div className="master-profile__actions">
          <button
            type="button"
            className="btn-secondary master-profile__action-btn"
            onClick={openFilePicker}
            disabled={photoUploading}
          >
            {photoUploading ? "Загружаем…" : COPY.buttons.editPhoto}
          </button>
          <button
            type="button"
            className="btn-secondary master-profile__action-btn"
            onClick={openBioEditor}
          >
            {COPY.buttons.editBio}
          </button>
        </div>

        {/* Display-name change is owner-approved — disabled for now. */}
        <p
          className="master-profile__hint"
          title={COPY.displayNameTooltip}
          aria-label={COPY.displayNameTooltip}
        >
          {COPY.displayNameTooltip}
        </p>

        {photoErr ? (
          <p className="master-profile__error" role="alert">
            {photoErr}
          </p>
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handlePhotoSelect(file);
          }}
        />
      </ProfileSection>

      <ProfileSection title={COPY.sections.services}>
        <ServicesList master={master} />
        <p className="master-profile__hint">{COPY.services.helper}</p>
        <button
          type="button"
          className="btn-secondary master-profile__action-btn"
          onClick={goToWriteOwner}
        >
          {COPY.buttons.writeToOwner}
        </button>
      </ProfileSection>

      <ProfileSection title={COPY.sections.workHours}>
        <p className="master-profile__hint">
          {COPY.workHours.fallback}
        </p>
        <button
          type="button"
          className="btn-secondary master-profile__action-btn"
          onClick={goToScheduleRequest}
        >
          {COPY.buttons.requestScheduleChange}
        </button>
      </ProfileSection>

      <ProfileSection title={COPY.sections.reviews}>
        <div
          className="callout"
          role="status"
          title={COPY.reviewsTooltip}
        >
          <p style={{ margin: 0 }}>{COPY.reviews.placeholder}</p>
        </div>
      </ProfileSection>

      {/*
       * M7 entry-point per spec §M7 line 780. Placed in a dedicated
       * «НАСТРОЙКИ» section at the bottom of the profile — the spec
       * locates the link generically as «Profile → «Уведомления»» but
       * doesn't pin a slot inside the M4 layout. Bottom of profile +
       * its own section header gives it visibility without crowding
       * the read-mostly «УСЛУГИ» / «ОТЗЫВЫ» blocks. M8 (App settings)
       * isn't built yet — when it ships, it will deep-link here.
       */}
      <ProfileSection title={COPY.sections.settings}>
        <button
          type="button"
          className="btn-secondary master-profile__action-btn"
          onClick={goToInternalChatList}
        >
          {COPY.buttons.internalChat}
        </button>
        <p className="master-profile__hint">{COPY.internalChatHint}</p>
        <button
          type="button"
          className="btn-secondary master-profile__action-btn"
          onClick={goToNotificationSettings}
        >
          {COPY.buttons.notificationSettings}
        </button>
        {/*
         * M8 minimal — logout-only app settings. Full M8 layout
         * (theme/font/help/about/version) deferred to a post-pilot
         * FOLLOW_UP issue per TL plan Step 2.
         */}
        <button
          type="button"
          className="btn-secondary master-profile__action-btn"
          onClick={goToAppSettings}
        >
          {COPY.buttons.appSettings}
        </button>
      </ProfileSection>

      <MasterTabBar
        unreadCount={0}
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />

      {editor !== null ? (
        <BioEditorSheet
          state={editor}
          onChange={updateBio}
          onCancel={cancelBioEditor}
          onSave={() => void saveBio()}
        />
      ) : null}

      <Snackbar
        visible={toast.length > 0}
        message={toast}
        durationMs={2000}
        onTimeout={() => setToast("")}
        onDismiss={() => setToast("")}
      />
    </ProfileFrame>
  );
}

// --- Sub-components -----------------------------------------------------

function ProfileFrame({ children }: { children: React.ReactNode }) {
  return <div className="master-profile">{children}</div>;
}

function ProfileSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="master-profile__section">
      <h2 className="master-profile__section-title">{title}</h2>
      {children}
    </section>
  );
}

function ProfileHeaderRow({ master }: { master: MasterMeMaster }) {
  const init = useMemo(() => initials(master.name), [master.name]);
  return (
    <div className="master-profile__header-row">
      <div className="master-profile__avatar" aria-hidden="true">
        {master.photo_url ? (
          <img src={master.photo_url} alt="" />
        ) : (
          <span>{init}</span>
        )}
      </div>
      <div className="master-profile__identity">
        <div className="master-profile__name">{master.name || "—"}</div>
        {master.specialization ? (
          <div className="master-profile__specialization">
            {master.specialization}
          </div>
        ) : null}
        {master.bio ? (
          <blockquote className="master-profile__bio-quote">
            «{master.bio}»
          </blockquote>
        ) : (
          <p className="master-profile__bio-empty">
            «О себе» пока не заполнено.
          </p>
        )}
      </div>
    </div>
  );
}

function ServicesList({ master }: { master: MasterMeMaster }) {
  if (!master.services || master.services.length === 0) {
    return (
      <p className="master-profile__hint">{COPY.services.empty}</p>
    );
  }
  return (
    <ul className="master-profile__services">
      {master.services.map((s) => {
        const duration = formatDuration(s.duration_min);
        return (
          <li key={s.id} className="master-profile__service-item">
            <span>{s.name}</span>
            {duration ? (
              <span className="master-profile__service-meta">
                {" "}
                · {duration}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function BioEditorSheet({
  state,
  onChange,
  onCancel,
  onSave,
}: {
  state: BioEditorState;
  onChange: (v: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const remaining = MASTER_PROFILE_BIO_MAX - state.value.length;
  const overLimit = remaining < 0;
  return (
    <div className="master-profile__sheet" role="dialog" aria-modal="true">
      <div className="master-profile__sheet-card">
        <h3 className="master-profile__sheet-title">{COPY.bioEdit.title}</h3>
        <textarea
          className="master-profile__textarea"
          value={state.value}
          onChange={(e) => onChange(e.target.value)}
          rows={5}
          maxLength={MASTER_PROFILE_BIO_MAX + 50}
          placeholder={COPY.bioEdit.placeholder}
          disabled={state.saving}
          aria-label={COPY.bioEdit.title}
        />
        <div
          className={
            overLimit
              ? "master-profile__counter master-profile__counter--bad"
              : "master-profile__counter"
          }
          aria-live="polite"
        >
          {state.value.length} / {MASTER_PROFILE_BIO_MAX}
        </div>
        {state.err ? (
          <p className="master-profile__error" role="alert">
            {state.err}
          </p>
        ) : null}
        <div className="master-profile__sheet-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={state.saving}
          >
            {COPY.buttons.cancel}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onSave}
            disabled={state.saving || overLimit}
          >
            {state.saving ? "Сохраняем…" : COPY.buttons.save}
          </button>
        </div>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="master-profile__skeleton-wrap" aria-busy="true">
      <p className="master-profile__loading-label">{COPY.states.loading}</p>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "60%", height: "1.2em" }} />
        <div
          className="skeleton"
          style={{ width: "40%", height: "1em", marginTop: 8 }}
        />
      </div>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "70%", height: "1em" }} />
      </div>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "50%", height: "1em" }} />
      </div>
    </div>
  );
}

function ErrorBanner({
  err: _err,
  onRetry,
}: {
  err: unknown;
  onRetry: () => void;
}) {
  return (
    <section className="master-profile__section">
      <h2 className="master-profile__section-title">
        {COPY.states.errorTitle}
      </h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.states.errorBody}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {COPY.buttons.retry}
          </button>
        </div>
      </div>
    </section>
  );
}

function OfflineBanner() {
  return (
    <div
      className="callout callout--danger"
      role="alert"
      style={{ margin: "var(--s-2) var(--s-3)" }}
    >
      <p style={{ margin: 0 }}>{COPY.states.offlineBanner}</p>
    </div>
  );
}
