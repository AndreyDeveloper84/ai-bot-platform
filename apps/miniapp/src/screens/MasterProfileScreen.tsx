/**
 * Экран 07 «Профиль мастера» — макет 6 (DRF-1814, часть B из трёх).
 * Route: /master/profile и /solo/profile.
 *
 * Карта разрывов: `Ayla/docs/GAP_MAP_MASTER_ONBOARDING_2026-09-12.md` §2.5 (P57–P67), §3.
 * Контракт: `GET /master/profile/card` (часть A, `apps/master_api/views_profile_card.py`).
 *
 * Блоки макета 6 и что здесь заперто:
 *   6.1 фото (необязательно сейчас, обязательно при публикации — решает readiness,
 *       не этот экран) + подсказки; имя с «Изменить»; «О себе» ≤ `limits.bio`;
 *   6.2 кроп 1:1 — `PhotoCropSheet` (камера/галерея, зум, поворот, заменить);
 *   6.3 работы ≤ `limits.portfolio_count`, удаление, «Пропустить пока»;
 *   6.4 предпросмотр — ТОТ ЖЕ `MasterCard`, что видит клиент. Бейдж «Принимает
 *       сегодня» рисуется только при `accepts_today === true` с сервера (реальный
 *       слот на сегодня); чипы — `categories` (категории выбранных шаблонов);
 *       одной подписи-специализации нет; рейтинг — только настоящий (`MasterCard`).
 *
 * Лимиты — данные контракта (`limits`), не литералы экрана (§3 карты): 280 из
 * §M4 снят вместе с `MASTER_PROFILE_BIO_MAX`. Счётчик работ — из ответа сервера.
 *
 * Ниже 6.x остаются разделы §M4, которых макет 6 не касается: услуги
 * (реальные offers из `/me`), рабочее время, отзывы, настройки.
 *
 * Bridge API:
 *   - BackButton.show() → /master/dashboard
 *   - enableClosingConfirmation() пока открыт редактор или идёт загрузка
 *   - hapticSelection() на открытие, hapticNotify на исход
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { MasterCard } from "../components/MasterCard";
import { MasterTabBar } from "../components/MasterTabBar";
import { CROP_COPY, PhotoCropSheet } from "../components/PhotoCropSheet";
import { Snackbar } from "../components/Snackbar";
import { ApiError, type Master } from "../lib/api";
import {
  MASTER_PROFILE_PHOTO_MIME_ALLOWLIST,
  deletePortfolioItem,
  getMasterMe,
  getMasterProfileCard,
  getPortfolio,
  patchMasterProfile,
  uploadMasterProfilePhoto,
  uploadPortfolioPhoto,
  type MasterMeMaster,
  type MasterMeResponse,
  type MasterProfileCard,
  type PortfolioList,
} from "../lib/master-api";
import {
  hapticNotify,
  hapticSelection,
  onBackButton,
  setBackButton,
  setClosingConfirmation,
} from "../lib/max-sdk";

// --- Копия -----------------------------------------------------------------

export const PROFILE_COPY = {
  header: "Профиль",
  sections: {
    photoAndName: "Фото и имя",
    portfolio: "Работы",
    preview: "Как видят клиенты",
    services: "━━ УСЛУГИ ━━━━━━━━━━━━━━━━━━",
    workHours: "━━ РАБОЧЕЕ ВРЕМЯ ━━━━━━━━━━━━",
    reviews: "━━ ОТЗЫВЫ КЛИЕНТОВ ━━━━━━━━━━━",
    settings: "━━ НАСТРОЙКИ ━━━━━━━━━━━━━━━",
  },
  photoHints: [
    "Настоящее фото — вас должны узнать в салоне.",
    "Без фильтров и логотипов, лицо хорошо видно.",
  ],
  photoOptional: "Фото можно добавить позже — но без него профиль не опубликуется.",
  buttons: {
    takePhoto: "Сделать фото",
    pickPhoto: "Выбрать из галереи",
    editName: "Изменить",
    editBio: "Изменить «О себе»",
    addWork: "Добавить фото работы",
    skipPortfolio: "Пропустить пока",
    writeToOwner: "Написать Карине ›",
    requestScheduleChange: "Запросить изменение ›",
    notificationSettings: "Настройки уведомлений ›",
    internalChat: "Со студией ›",
    appSettings: "Настройки приложения ›",
    save: "Сохранить",
    cancel: "Отмена",
    retry: "Попробовать снова",
  },
  nameEdit: {
    title: "Имя",
    placeholder: "Как вас называть клиентам",
    tooShort: (min: number) => `Имя короче ${min} символов нельзя.`,
  },
  bioEdit: {
    title: "О себе",
    placeholder: "Например: 7 лет в маникюре, люблю аккуратную классику…",
    empty: "«О себе» пока не заполнено.",
  },
  portfolio: {
    counter: (count: number, limit: number) => `${count} из ${limit}`,
    hint: "До десяти фото работ. Клиенты смотрят их до записи.",
    skipped: "Работы можно добавить позже — из профиля.",
    removeAria: "Удалить фото работы",
    empty: "Пока ни одной работы.",
  },
  preview: {
    acceptsToday: "Принимает сегодня",
    hint: "Так вашу карточку видят клиенты. Рейтинг появится после первых отзывов.",
  },
  crop: CROP_COPY,
  services: {
    helper: "Хотите добавить или убрать?",
    empty: "Услуги ещё не назначены.",
  },
  workHours: {
    fallback: "Информацию о рабочем времени уточните у администратора.",
  },
  reviews: {
    placeholder: "Отзывы появятся после первых визитов.",
  },
  reviewsTooltip: "Отзывы появятся позже — мы готовим этот раздел.",
  internalChatHint: "Личный канал общения с админами студии.",
  toasts: {
    saved: "✓ Сохранено",
    photoSaved: "✓ Фото обновлено",
    workAdded: "✓ Работа добавлена",
    workRemoved: "✓ Фото удалено",
  },
  states: {
    loading: "Загружаем профиль…",
    errorTitle: "Не получилось загрузить",
    errorBody: "Не получилось загрузить ваш профиль. Проверьте интернет и попробуйте снова.",
    saveError: "Не удалось сохранить. Попробуйте ещё раз.",
    photoTooLarge: (mb: number) => `Фото больше ${mb} МБ. Уменьшите размер.`,
    photoBadMime: "Поддерживаются JPG / PNG / WebP",
    photoNetwork: "Не получилось загрузить фото. Проверьте интернет и попробуйте снова.",
    bioTooLong: (max: number) => `Длиннее ${max} символов нельзя.`,
    offlineBanner: "Нет связи. Сохраним, как только сеть появится.",
  },
};

// --- Helpers --------------------------------------------------------------

function initials(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("");
}

function formatDuration(min: number | null): string {
  if (typeof min !== "number" || !Number.isFinite(min) || min <= 0) return "";
  return `${Math.round(min)} мин`;
}

const megabytes = (bytes: number): number => Math.max(1, Math.round(bytes / (1024 * 1024)));

// --- Модель состояния ------------------------------------------------------

interface TextEditorState {
  value: string;
  saving: boolean;
  err: string;
}

interface ReadyData {
  me: MasterMeResponse;
  card: MasterProfileCard;
  portfolio: PortfolioList;
}

type Phase = { kind: "loading" } | { kind: "ready"; data: ReadyData } | { kind: "error"; err: unknown };

// --- Компонент -------------------------------------------------------------

export function MasterProfileScreen() {
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [bioEditor, setBioEditor] = useState<TextEditorState | null>(null);
  const [nameEditor, setNameEditor] = useState<TextEditorState | null>(null);
  const [cropFile, setCropFile] = useState<File | null>(null);
  const [photoUploading, setPhotoUploading] = useState(false);
  const [photoErr, setPhotoErr] = useState("");
  const [workBusy, setWorkBusy] = useState(false);
  const [workErr, setWorkErr] = useState("");
  const [portfolioSkipped, setPortfolioSkipped] = useState(false);
  const [offlineBanner, setOfflineBanner] = useState(false);
  const [toast, setToast] = useState("");
  const galleryInputRef = useRef<HTMLInputElement | null>(null);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const workInputRef = useRef<HTMLInputElement | null>(null);

  const card = phase.kind === "ready" ? phase.data.card : null;
  const limits = card?.limits ?? null;

  const isDirty =
    (bioEditor !== null && bioEditor.value !== (card?.master.bio ?? "")) ||
    (nameEditor !== null && nameEditor.value !== (card?.master.name ?? "")) ||
    cropFile !== null;

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
    setClosingConfirmation(isDirty || photoUploading || workBusy);
  }, [isDirty, photoUploading, workBusy]);

  // --- Загрузка: /me (услуги, права) + карточка (владелец полей) + работы ---
  const fetchAll = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const [me, cardData, portfolio] = await Promise.all([
        getMasterMe(),
        getMasterProfileCard(),
        getPortfolio(),
      ]);
      setPhase({ kind: "ready", data: { me, card: cardData, portfolio } });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  const patchCard = useCallback((patch: Partial<MasterProfileCard["master"]>) => {
    setPhase((curr) =>
      curr.kind === "ready"
        ? {
            kind: "ready",
            data: { ...curr.data, card: { ...curr.data.card, master: { ...curr.data.card.master, ...patch } } },
          }
        : curr,
    );
  }, []);

  const refreshPortfolio = useCallback(async () => {
    const portfolio = await getPortfolio();
    setPhase((curr) => (curr.kind === "ready" ? { kind: "ready", data: { ...curr.data, portfolio } } : curr));
  }, []);

  // --- «О себе» ---
  const openBioEditor = useCallback(() => {
    if (!card) return;
    hapticSelection();
    setBioEditor({ value: card.master.bio ?? "", saving: false, err: "" });
  }, [card]);

  const saveBio = useCallback(async () => {
    if (!bioEditor || !limits) return;
    const value = bioEditor.value;
    if (value.length > limits.bio) {
      setBioEditor({ ...bioEditor, err: PROFILE_COPY.states.bioTooLong(limits.bio) });
      hapticNotify("error");
      return;
    }
    setBioEditor({ ...bioEditor, saving: true, err: "" });
    setOfflineBanner(false);
    try {
      const res = await patchMasterProfile({ bio: value });
      hapticNotify("success");
      patchCard({ bio: res.master.bio, photo_url: res.master.photo_url });
      setBioEditor(null);
      setToast(PROFILE_COPY.toasts.saved);
    } catch (e) {
      if (e instanceof ApiError) {
        setBioEditor({ ...bioEditor, saving: false, err: e.detail || PROFILE_COPY.states.saveError });
      } else {
        setOfflineBanner(true);
        setBioEditor({ ...bioEditor, saving: false, err: "" });
      }
      hapticNotify("error");
    }
  }, [bioEditor, limits, patchCard]);

  // --- Имя ---
  const openNameEditor = useCallback(() => {
    if (!card) return;
    hapticSelection();
    setNameEditor({ value: card.master.name ?? "", saving: false, err: "" });
  }, [card]);

  const saveName = useCallback(async () => {
    if (!nameEditor || !limits) return;
    const value = nameEditor.value.trim();
    if (value.length < limits.display_name_min) {
      setNameEditor({ ...nameEditor, err: PROFILE_COPY.nameEdit.tooShort(limits.display_name_min) });
      hapticNotify("error");
      return;
    }
    setNameEditor({ ...nameEditor, saving: true, err: "" });
    setOfflineBanner(false);
    try {
      const res = await patchMasterProfile({ display_name: value });
      hapticNotify("success");
      patchCard({ name: res.master.name });
      setNameEditor(null);
      setToast(PROFILE_COPY.toasts.saved);
    } catch (e) {
      if (e instanceof ApiError) {
        setNameEditor({ ...nameEditor, saving: false, err: e.detail || PROFILE_COPY.states.saveError });
      } else {
        setOfflineBanner(true);
        setNameEditor({ ...nameEditor, saving: false, err: "" });
      }
      hapticNotify("error");
    }
  }, [nameEditor, limits, patchCard]);

  // --- Фото: выбор → кроп → загрузка ---
  const acceptPhotoFile = useCallback(
    (file: File, maxBytes: number): boolean => {
      if (file.size > maxBytes) {
        setPhotoErr(PROFILE_COPY.states.photoTooLarge(megabytes(maxBytes)));
        hapticNotify("error");
        return false;
      }
      if (!MASTER_PROFILE_PHOTO_MIME_ALLOWLIST.has((file.type || "").toLowerCase())) {
        setPhotoErr(PROFILE_COPY.states.photoBadMime);
        hapticNotify("error");
        return false;
      }
      return true;
    },
    [],
  );

  const handleAvatarSelect = useCallback(
    (file: File) => {
      if (!limits) return;
      setPhotoErr("");
      if (!acceptPhotoFile(file, limits.avatar_bytes)) return;
      hapticSelection();
      setCropFile(file);
    },
    [limits, acceptPhotoFile],
  );

  const applyCrop = useCallback(
    async (square: Blob) => {
      setPhotoUploading(true);
      setPhotoErr("");
      try {
        const file = new File([square], "avatar.jpg", { type: square.type || "image/jpeg" });
        const res = await uploadMasterProfilePhoto(file);
        hapticNotify("success");
        patchCard({ photo_url: res.master.photo_url, bio: res.master.bio });
        setCropFile(null);
        setToast(PROFILE_COPY.toasts.photoSaved);
      } catch (e) {
        setPhotoErr(e instanceof ApiError ? e.detail || PROFILE_COPY.states.photoNetwork : PROFILE_COPY.states.photoNetwork);
        hapticNotify("error");
      } finally {
        setPhotoUploading(false);
        if (galleryInputRef.current) galleryInputRef.current.value = "";
        if (cameraInputRef.current) cameraInputRef.current.value = "";
      }
    },
    [patchCard],
  );

  // --- Работы ---
  const handleWorkSelect = useCallback(
    async (file: File) => {
      if (!limits) return;
      setWorkErr("");
      const okSize = file.size <= limits.portfolio_bytes;
      if (!okSize) {
        setWorkErr(PROFILE_COPY.states.photoTooLarge(megabytes(limits.portfolio_bytes)));
        hapticNotify("error");
        return;
      }
      if (!MASTER_PROFILE_PHOTO_MIME_ALLOWLIST.has((file.type || "").toLowerCase())) {
        setWorkErr(PROFILE_COPY.states.photoBadMime);
        hapticNotify("error");
        return;
      }
      setWorkBusy(true);
      try {
        await uploadPortfolioPhoto(file);
        await refreshPortfolio();
        hapticNotify("success");
        setToast(PROFILE_COPY.toasts.workAdded);
      } catch (e) {
        setWorkErr(e instanceof ApiError ? e.detail || PROFILE_COPY.states.photoNetwork : PROFILE_COPY.states.photoNetwork);
        hapticNotify("error");
      } finally {
        setWorkBusy(false);
        if (workInputRef.current) workInputRef.current.value = "";
      }
    },
    [limits, refreshPortfolio],
  );

  const removeWork = useCallback(
    async (itemId: string) => {
      setWorkErr("");
      setWorkBusy(true);
      try {
        await deletePortfolioItem(itemId);
        await refreshPortfolio();
        hapticNotify("success");
        setToast(PROFILE_COPY.toasts.workRemoved);
      } catch (e) {
        setWorkErr(e instanceof ApiError ? e.detail || PROFILE_COPY.states.saveError : PROFILE_COPY.states.saveError);
        hapticNotify("error");
      } finally {
        setWorkBusy(false);
      }
    },
    [refreshPortfolio],
  );

  // --- Навигация ---
  const goToScheduleRequest = useCallback(() => {
    hapticSelection();
    navigate("/master/schedule");
  }, [navigate]);
  const goToNotificationSettings = useCallback(() => {
    hapticSelection();
    navigate("/master/settings/notifications");
  }, [navigate]);
  const goToAppSettings = useCallback(() => {
    hapticSelection();
    navigate("/master/settings");
  }, [navigate]);
  const goToWriteOwner = useCallback(() => {
    hapticSelection();
    navigate("/master/internal-chat?new=1&topic=general");
  }, [navigate]);
  const goToInternalChatList = useCallback(() => {
    hapticSelection();
    navigate("/master/internal-chat");
  }, [navigate]);

  // --- Ветки рендера ---
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
        <ErrorBanner onRetry={() => void fetchAll()} />
      </ProfileFrame>
    );
  }

  const { me, card: cardData, portfolio } = phase.data;
  const master = cardData.master;
  const previewMaster: Master = {
    id: master.id,
    name: master.name,
    specialization: "",
    bio: master.bio,
    experience: "",
    rating: null,
    photo_url: master.photo_url,
  };
  const portfolioFull = portfolio.count >= portfolio.limit;

  return (
    <ProfileFrame>
      {offlineBanner ? <OfflineBanner /> : null}

      {/* 6.1 — фото и имя */}
      <ProfileSection title={PROFILE_COPY.sections.photoAndName}>
        <div className="master-profile__header-row">
          <div className="master-profile__avatar" aria-hidden="true">
            {master.photo_url ? <img src={master.photo_url} alt="" /> : <span>{initials(master.name)}</span>}
          </div>
          <div className="master-profile__identity">
            <div className="master-profile__name-row">
              <div className="master-profile__name">{master.name || "—"}</div>
              <button type="button" className="master-profile__link-btn" onClick={openNameEditor}>
                {PROFILE_COPY.buttons.editName}
              </button>
            </div>
            {master.bio ? (
              <blockquote className="master-profile__bio-quote">«{master.bio}»</blockquote>
            ) : (
              <p className="master-profile__bio-empty">{PROFILE_COPY.bioEdit.empty}</p>
            )}
          </div>
        </div>

        <ul className="master-profile__hints">
          {PROFILE_COPY.photoHints.map((hint) => (
            <li key={hint}>{hint}</li>
          ))}
        </ul>
        <p className="master-profile__hint">{PROFILE_COPY.photoOptional}</p>

        <div className="master-profile__actions">
          <button
            type="button"
            className="btn-secondary master-profile__action-btn"
            onClick={() => {
              hapticSelection();
              cameraInputRef.current?.click();
            }}
            disabled={photoUploading}
          >
            {PROFILE_COPY.buttons.takePhoto}
          </button>
          <button
            type="button"
            className="btn-secondary master-profile__action-btn"
            onClick={() => {
              hapticSelection();
              galleryInputRef.current?.click();
            }}
            disabled={photoUploading}
          >
            {photoUploading ? "Загружаем…" : PROFILE_COPY.buttons.pickPhoto}
          </button>
          <button type="button" className="btn-secondary master-profile__action-btn" onClick={openBioEditor}>
            {PROFILE_COPY.buttons.editBio}
          </button>
        </div>

        {photoErr ? (
          <p className="master-profile__error" role="alert">
            {photoErr}
          </p>
        ) : null}

        <input
          ref={galleryInputRef}
          data-role="avatar"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleAvatarSelect(file);
          }}
        />
        <input
          ref={cameraInputRef}
          data-role="avatar-camera"
          type="file"
          accept="image/*"
          capture="user"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleAvatarSelect(file);
          }}
        />
      </ProfileSection>

      {/* 6.3 — работы */}
      <ProfileSection title={PROFILE_COPY.sections.portfolio}>
        {portfolioSkipped && portfolio.count === 0 ? (
          <p className="master-profile__hint">{PROFILE_COPY.portfolio.skipped}</p>
        ) : (
          <>
            <div className="master-profile__portfolio-head">
              <span className="master-profile__counter" aria-live="polite">
                {PROFILE_COPY.portfolio.counter(portfolio.count, portfolio.limit)}
              </span>
              <span className="master-profile__hint">{PROFILE_COPY.portfolio.hint}</span>
            </div>
            {portfolio.items.length === 0 ? (
              <p className="master-profile__hint">{PROFILE_COPY.portfolio.empty}</p>
            ) : (
              <ul className="master-profile__portfolio">
                {portfolio.items.map((item) => (
                  <li key={item.id} className="master-profile__work">
                    <img src={item.image_url} alt="" />
                    <button
                      type="button"
                      className="master-profile__work-remove"
                      aria-label={PROFILE_COPY.portfolio.removeAria}
                      onClick={() => void removeWork(item.id)}
                      disabled={workBusy}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {workErr ? (
              <p className="master-profile__error" role="alert">
                {workErr}
              </p>
            ) : null}
            <div className="master-profile__actions">
              {!portfolioFull ? (
                <button
                  type="button"
                  className="btn-secondary master-profile__action-btn"
                  onClick={() => {
                    hapticSelection();
                    workInputRef.current?.click();
                  }}
                  disabled={workBusy}
                >
                  {workBusy ? "Загружаем…" : PROFILE_COPY.buttons.addWork}
                </button>
              ) : null}
              {portfolio.count === 0 ? (
                <button
                  type="button"
                  className="master-profile__link-btn"
                  onClick={() => {
                    hapticSelection();
                    setPortfolioSkipped(true);
                  }}
                >
                  {PROFILE_COPY.buttons.skipPortfolio}
                </button>
              ) : null}
            </div>
            <input
              ref={workInputRef}
              data-role="work"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleWorkSelect(file);
              }}
            />
          </>
        )}
      </ProfileSection>

      {/* 6.4 — предпросмотр тем же MasterCard */}
      <ProfileSection title={PROFILE_COPY.sections.preview}>
        <div className="master-profile__preview">
          <MasterCard
            master={previewMaster}
            acceptsToday={cardData.accepts_today}
            categories={cardData.categories}
            onSelect={() => {}}
          />
        </div>
        <p className="master-profile__hint">{PROFILE_COPY.preview.hint}</p>
      </ProfileSection>

      <ProfileSection title={PROFILE_COPY.sections.services}>
        <ServicesList master={me.master} />
        <p className="master-profile__hint">{PROFILE_COPY.services.helper}</p>
        <button type="button" className="btn-secondary master-profile__action-btn" onClick={goToWriteOwner}>
          {PROFILE_COPY.buttons.writeToOwner}
        </button>
      </ProfileSection>

      <ProfileSection title={PROFILE_COPY.sections.workHours}>
        <p className="master-profile__hint">{PROFILE_COPY.workHours.fallback}</p>
        <button type="button" className="btn-secondary master-profile__action-btn" onClick={goToScheduleRequest}>
          {PROFILE_COPY.buttons.requestScheduleChange}
        </button>
      </ProfileSection>

      <ProfileSection title={PROFILE_COPY.sections.reviews}>
        <div className="callout" role="status" title={PROFILE_COPY.reviewsTooltip}>
          <p style={{ margin: 0 }}>{PROFILE_COPY.reviews.placeholder}</p>
        </div>
      </ProfileSection>

      <ProfileSection title={PROFILE_COPY.sections.settings}>
        <button type="button" className="btn-secondary master-profile__action-btn" onClick={goToInternalChatList}>
          {PROFILE_COPY.buttons.internalChat}
        </button>
        <p className="master-profile__hint">{PROFILE_COPY.internalChatHint}</p>
        <button type="button" className="btn-secondary master-profile__action-btn" onClick={goToNotificationSettings}>
          {PROFILE_COPY.buttons.notificationSettings}
        </button>
        <button type="button" className="btn-secondary master-profile__action-btn" onClick={goToAppSettings}>
          {PROFILE_COPY.buttons.appSettings}
        </button>
      </ProfileSection>

      <MasterTabBar unreadCount={0} scheduleHasPendingChange={false} profileHasOwnerPendingChange={false} />

      {bioEditor !== null && limits ? (
        <TextEditorSheet
          title={PROFILE_COPY.bioEdit.title}
          placeholder={PROFILE_COPY.bioEdit.placeholder}
          multiline
          max={limits.bio}
          state={bioEditor}
          onChange={(value) => setBioEditor((c) => (c ? { ...c, value, err: "" } : c))}
          onCancel={() => setBioEditor(null)}
          onSave={() => void saveBio()}
        />
      ) : null}

      {nameEditor !== null && limits ? (
        <TextEditorSheet
          title={PROFILE_COPY.nameEdit.title}
          placeholder={PROFILE_COPY.nameEdit.placeholder}
          multiline={false}
          state={nameEditor}
          onChange={(value) => setNameEditor((c) => (c ? { ...c, value, err: "" } : c))}
          onCancel={() => setNameEditor(null)}
          onSave={() => void saveName()}
        />
      ) : null}

      {cropFile !== null ? (
        <PhotoCropSheet
          file={cropFile}
          busy={photoUploading}
          onApply={(square) => void applyCrop(square)}
          onReplace={() => {
            setCropFile(null);
            galleryInputRef.current?.click();
          }}
          onCancel={() => setCropFile(null)}
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

// --- Подкомпоненты ---------------------------------------------------------

function ProfileFrame({ children }: { children: React.ReactNode }) {
  return <div className="master-profile">{children}</div>;
}

function ProfileSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="master-profile__section">
      <h2 className="master-profile__section-title">{title}</h2>
      {children}
    </section>
  );
}

function ServicesList({ master }: { master: MasterMeMaster }) {
  if (!master.services || master.services.length === 0) {
    return <p className="master-profile__hint">{PROFILE_COPY.services.empty}</p>;
  }
  return (
    <ul className="master-profile__services">
      {master.services.map((s) => {
        const duration = formatDuration(s.duration_min);
        return (
          <li key={s.id} className="master-profile__service-item">
            <span>{s.name}</span>
            {duration ? <span className="master-profile__service-meta"> · {duration}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function TextEditorSheet({
  title,
  placeholder,
  multiline,
  max,
  state,
  onChange,
  onCancel,
  onSave,
}: {
  title: string;
  placeholder: string;
  multiline: boolean;
  /** Лимит из контракта; счётчик и блокировка «Сохранить» — от него. */
  max?: number;
  state: TextEditorState;
  onChange: (v: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const overLimit = typeof max === "number" && state.value.length > max;
  const counterClass = useMemo(
    () => (overLimit ? "master-profile__counter master-profile__counter--bad" : "master-profile__counter"),
    [overLimit],
  );
  return (
    <div className="master-profile__sheet" role="dialog" aria-modal="true" aria-label={title}>
      <div className="master-profile__sheet-card">
        <h3 className="master-profile__sheet-title">{title}</h3>
        {multiline ? (
          <textarea
            className="master-profile__textarea"
            value={state.value}
            onChange={(e) => onChange(e.target.value)}
            rows={5}
            maxLength={typeof max === "number" ? max + 50 : undefined}
            placeholder={placeholder}
            disabled={state.saving}
            aria-label={title}
          />
        ) : (
          <input
            className="master-profile__input"
            type="text"
            value={state.value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            disabled={state.saving}
            aria-label={title}
          />
        )}
        {typeof max === "number" ? (
          <div className={counterClass} aria-live="polite">
            {state.value.length} / {max}
          </div>
        ) : null}
        {state.err ? (
          <p className="master-profile__error" role="alert">
            {state.err}
          </p>
        ) : null}
        <div className="master-profile__sheet-actions">
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={state.saving}>
            {PROFILE_COPY.buttons.cancel}
          </button>
          <button type="button" className="btn-primary" onClick={onSave} disabled={state.saving || overLimit}>
            {state.saving ? "Сохраняем…" : PROFILE_COPY.buttons.save}
          </button>
        </div>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="master-profile__skeleton-wrap" aria-busy="true">
      <p className="master-profile__loading-label">{PROFILE_COPY.states.loading}</p>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "60%", height: "1.2em" }} />
        <div className="skeleton" style={{ width: "40%", height: "1em", marginTop: 8 }} />
      </div>
      <div className="m-card m-card--skel">
        <div className="skeleton" style={{ width: "70%", height: "1em" }} />
      </div>
    </div>
  );
}

function ErrorBanner({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="master-profile__section">
      <h2 className="master-profile__section-title">{PROFILE_COPY.states.errorTitle}</h2>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{PROFILE_COPY.states.errorBody}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry}>
            {PROFILE_COPY.buttons.retry}
          </button>
        </div>
      </div>
    </section>
  );
}

function OfflineBanner() {
  return (
    <div className="callout callout--danger" role="alert" style={{ margin: "var(--s-2) var(--s-3)" }}>
      <p style={{ margin: 0 }}>{PROFILE_COPY.states.offlineBanner}</p>
    </div>
  );
}
