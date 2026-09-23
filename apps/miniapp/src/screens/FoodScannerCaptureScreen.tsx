/**
 * F1 Capture — Customer Food Scanner Phase B (Tier 1 Priority 7).
 *
 * Route: `/customer/food-scanner/capture`
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §2 (F1 layout) +
 * §11 (a11y). Wraps a 152-ФЗ consent gate (spec §2 + memory
 * `project_variant_b_wellness_mvp`); once accepted, never re-prompts.
 *
 * # Why both camera + gallery inputs
 *
 * Spec §2 + Q-BACK-3: MAX webview MAY ignore `capture="environment"`
 * on iOS; the only safe path is two distinct `<input type="file">`
 * elements, both `accept="image/*"`, the camera one ALSO carrying
 * `capture`. iOS auto-falls back to the file picker when capture is
 * absent — calm degradation, no surprise dialog.
 *
 * # Flow
 *
 *   ↳ consent gate (first visit) → accept / decline (= back to dashboard)
 *   ↳ meal-type chip selection (default by local time bucket)
 *   ↳ photo picker (camera OR gallery)
 *   ↳ on file pick → navigate F2 with the photo in router state
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  ImageStripUnsupportedError,
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  defaultMealTypeForHour,
  fetchDiaryConsentGate,
  grantConsent,
  stripImageMetadata,
  type MealType,
} from "../lib/food-scanner";
import {
  BUTTON_DECLINE,
  BUTTON_GRANT,
  DISCLOSURE_BODY,
  DISCLOSURE_HEADLINE,
  FOOD_DIARY_DISCLOSURE_VERSION,
} from "../lib/food-diary-disclosure";
import { Skeleton } from "../components/Skeleton";
import { MANUAL_ROUTE } from "./FoodScannerManualScreen";
import { StateError } from "../components/StateError";
import { useScreenBack } from "../hooks/useScreenBack";
import { backToOrigin } from "../lib/screen-back";

const MEAL_TYPES: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

interface RouterIn {
  /** When set, consent-accept bounces back here instead of staying on F1. */
  returnTo?: string;
  mealType?: MealType;
}

export function FoodScannerCaptureScreen() {
  const navigate = useNavigate();

  const location = useLocation();
  const incoming = (location.state ?? {}) as RouterIn;
  // Возврат (DRF-1493) — туда, откуда пришли. Съёмку открывают и с
  // Главной, и из «Дневника» (`FoodScannerDiaryScreen` давно передаёт
  // `returnTo`), и до DRF-2349 оба входа уводило на Главную. Нет
  // происхождения — прежний адрес, прежнее поведение.
  const onBack = useScreenBack(backToOrigin(location.state, "/customer/main"));
  // Согласие спрашивается у СЕРВЕРА, а не у браузера (DRF-1564).
  //
  // `null` — «согласия нет», и экран показывает гейт. Пока ответ не
  // пришёл, состояние `undefined`: экран НЕ показывает ни гейт, ни
  // камеру. Показать гейт заранее значило бы переспросить согласие у
  // того, кто его уже дал, — а согласие переспрашивают только тогда,
  // когда его действительно нет.
  const [consentAt, setConsentAt] = useState<string | null | undefined>(
    undefined,
  );
  const [consentErr, setConsentErr] = useState<unknown>(null);
  // КАКОЙ путь согласия живой — со слов сервера. Не «разрешено ли»:
  // право устанавливает предикат на сервере, экран его только показывает.
  // По умолчанию `false` — то есть старый путь: не узнав ничего, экран
  // обязан вести себя как сегодня, а не как завтра.
  const [canonical, setCanonical] = useState<boolean>(false);

  const loadConsent = useCallback(async () => {
    setConsentErr(null);
    try {
      // Каким путём спрашивать, решает СЕРВЕР, а не сборка: правило
      // «поля нет — путь старый» живёт в одном месте, внутри
      // `fetchDiaryConsentGate`, и здесь не повторяется.
      const gate = await fetchDiaryConsentGate();
      setCanonical(gate.canonical);
      setConsentAt(gate.grantedAt);
    } catch (e) {
      // Не подставляем `null`: «не смогли спросить» — не «согласия
      // нет». Первое лечится повтором, второе — гейтом, и путать их
      // здесь значит спрашивать согласие на пустом месте.
      setConsentErr(e);
    }
  }, []);

  useEffect(() => {
    loadConsent();
  }, [loadConsent]);
  const [mealType, setMealType] = useState<MealType>(() =>
    defaultMealTypeForHour(new Date().getHours()),
  );
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [error, setError] = useState<string | null>(null);
  // Processing flag — re-encode + strip takes 200-800ms on mid-tier
  // hardware; without disabling the buttons the customer can fire a
  // second pick + race two async navigations (adversarial CR P3).
  const [processing, setProcessing] = useState<boolean>(false);
  const cameraInputRef = useRef<HTMLInputElement | null>(null);
  const galleryInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  const handleAcceptConsent = useCallback(async () => {
    // Момент выдачи берём из ответа сервера, а не из часов браузера:
    // гейт навыка читает ту же колонку, и два разных времени у одного
    // согласия — это два разных согласия.
    let now: string | null;
    try {
      // Ручка одна и версия одна для обоих текстов (DRF-2038 поверх
      // половины 1 DRF-1963): выдача идёт через `grantConsent` в любом
      // случае. Какой текст человек читал, решает `canonical` ниже, а
      // версия, под которой согласие ложится в реестр, — константа
      // половины 1; развилка v0/v1 у владельца.
      now = await grantConsent();
    } catch (e) {
      setConsentErr(e);
      return;
    }
    setConsentAt(now);
    // If the customer arrived via deep-link to a downstream surface
    // (e.g. /manual) and was bounced here for consent, return them
    // home after accepting (adversarial CR F1).
    if (incoming.returnTo) {
      navigate(incoming.returnTo, {
        replace: true,
        state: { mealType: incoming.mealType },
      });
    }
  }, [navigate, incoming.returnTo, incoming.mealType]);

  const handleDeclineConsent = useCallback(() => {
    navigate("/customer/main");
  }, [navigate]);

  const handleFile = useCallback(
    async (file: File | null | undefined) => {
      if (!file) return;
      if (processing) return; // ignore second tap mid-strip
      // Client-side size guard — spec §13 item 1; backend caps at 10 MiB.
      const MAX_BYTES = 10 * 1024 * 1024;
      if (file.size > MAX_BYTES) {
        setError("Фото слишком большое. Можно отправить файл до 10 МБ.");
        return;
      }
      if (!file.type.startsWith("image/")) {
        setError("Это не похоже на фото. Можно картинку?");
        return;
      }
      setError(null);
      setProcessing(true);
      // Strip EXIF (incl. GPS) BEFORE the photo leaves the device.
      // Follow-up #957 — without this, geo-tagged JPEGs leak meal
      // location to the backend before the delete-after-recognition
      // runs. Server-side strip is a separate W4 hardening; this is
      // defence-in-depth layer 1.
      //
      // FAIL-CLOSED: if strip fails (HEIC on Safari, decode error,
      // encode error), we REFUSE the upload rather than silent-pass
      // the original file with GPS inside. Customer gets a clear copy
      // and can switch camera format to JPEG.
      let cleanFile: File;
      try {
        cleanFile = await stripImageMetadata(file);
      } catch (e) {
        if (e instanceof ImageStripUnsupportedError) {
          setError(
            "Не получилось обработать фото. Если у тебя iPhone, попробуй переключить формат камеры на JPEG (Настройки → Камера → Форматы → «Наиболее совместимый») и сделать снимок ещё раз.",
          );
        } else {
          setError("Не получилось обработать фото. Попробуй ещё раз.");
        }
        setProcessing(false);
        return;
      }
      // Photo flows to F2 via router state (no global store needed).
      // DRF-2349 — происхождение едет вместе с фото: выход из потока
      // должен вернуть туда же, откуда в него вошли, а шагов в потоке
      // три, и адрес теряется на первом же из них.
      navigate("/customer/food-scanner/processing", {
        state: { photo: cleanFile, mealType, returnTo: incoming.returnTo },
      });
    },
    [navigate, mealType, processing, incoming.returnTo],
  );

  // ── render branches ────────────────────────────────────────────────
  // Три состояния согласия, и свести любые два нельзя:
  //   `undefined` — ещё не спросили: ждём, гейт не показываем;
  //   ошибка чтения — «не смогли спросить»: говорим об этом и даём
  //                   повтор, но согласие НЕ переспрашиваем;
  //   `null`      — согласия нет: гейт.
  if (consentErr !== null) {
    return (
      <div className="food-scanner-screen">
        <StateError err={consentErr} onRetry={loadConsent} />
      </div>
    );
  }
  if (consentAt === undefined) {
    return (
      <div className="food-scanner-screen">
        <Skeleton width="70%" height="1.1em" />
      </div>
    );
  }
  if (consentAt === null) {
    return (
      <ConsentGate
        canonical={canonical}
        onBack={onBack}
        onAccept={handleAcceptConsent}
        onDecline={handleDeclineConsent}
      />
    );
  }

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Скан еды</h1>
      </header>

      {offline && (
        <div className="records-screen__offline-banner" role="status">
          Для распознавания нужна сеть. Попробуй когда вернётся.
        </div>
      )}

      <main className="food-scanner-screen__main">
        <h2 className="food-scanner-screen__greeting">Что ешь сейчас?</h2>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-meal-type-h2"
        >
          <h3
            id="food-meal-type-h2"
            className="food-scanner-screen__section-heading"
          >
            Когда
          </h3>
          <div
            className="food-scanner-screen__meal-chips"
            role="radiogroup"
            aria-labelledby="food-meal-type-h2"
          >
            {MEAL_TYPES.map((mt) => (
              <button
                key={mt}
                type="button"
                role="radio"
                aria-checked={mealType === mt}
                className={`food-scanner-screen__chip${
                  mealType === mt ? " food-scanner-screen__chip--active" : ""
                }`}
                onClick={() => setMealType(mt)}
              >
                <span aria-hidden="true">{MEAL_TYPE_ICON[mt]}</span>
                <span>{MEAL_TYPE_LABEL[mt]}</span>
              </button>
            ))}
          </div>
        </section>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-photo-h2"
        >
          <h3
            id="food-photo-h2"
            className="food-scanner-screen__section-heading"
          >
            Фото
          </h3>
          <div className="food-scanner-screen__capture-zone">
            <p className="food-scanner-screen__capture-hint">
              Сделай фото или выбери из галереи
            </p>
            <div
              className="food-scanner-screen__capture-actions"
              aria-busy={processing}
            >
              <button
                type="button"
                className="btn-primary food-scanner-screen__capture-btn"
                disabled={offline || processing}
                onClick={() => cameraInputRef.current?.click()}
              >
                {processing ? "Обрабатываю фото…" : "Сделать фото"}
              </button>
              <button
                type="button"
                className="btn-secondary food-scanner-screen__capture-btn"
                disabled={offline || processing}
                onClick={() => galleryInputRef.current?.click()}
              >
                Из галереи
              </button>
            </div>
            {/* Camera input — iOS uses `capture` to open the native
                Camera app; if ignored (some iOS Safari versions),
                falls back to file picker silently. */}
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              style={{ display: "none" }}
              onChange={(e) => handleFile(e.target.files?.[0])}
              aria-hidden="true"
            />
            <input
              ref={galleryInputRef}
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={(e) => handleFile(e.target.files?.[0])}
              aria-hidden="true"
            />
          </div>
        </section>

        {error && (
          <div
            className="callout callout--danger food-scanner-screen__error"
            role="alert"
          >
            {error}
          </div>
        )}

        {/* DRF-2289 — фото не единственный путь: вход с Главной ведёт
            сюда, а ввод текстом — эта ссылка. Подпись — черновик для
            владельца. */}
        <button
          type="button"
          className="food-scanner-screen__text-link"
          disabled={processing}
          onClick={() => navigate(MANUAL_ROUTE)}
        >
          Записать текстом
        </button>

        <p className="food-scanner-screen__privacy">
          Фото нужно только чтобы узнать блюдо — удаляю сразу.
        </p>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 152-ФЗ consent gate (first visit only) — spec §2 + Profile R2 voice.
// ---------------------------------------------------------------------------

function ConsentGate({
  canonical,
  onBack,
  onAccept,
  onDecline,
}: {
  /**
   * Какой текст показывать. `false` — нынешний короткий текст, слово в
   * слово как до DRF-2038; `true` — каноническое раскрытие Z9. Пока
   * раскрытие имеет статус WORKING PRODUCT COPY, сервер объявляет канон
   * только под флагом, и по умолчанию экран обязан показывать старое.
   */
  canonical: boolean;
  /**
   * Возврат приходит готовым от экрана (DRF-1493).
   *
   * Своего `useScreenBack` здесь быть не должно: гейт живёт ВНУТРИ
   * `FoodScannerCaptureScreen`, который объявление уже сделал. Два
   * живых объявления давали два перехода на одно нажатие, а после
   * «разрешаю» гейт размонтировался и его cleanup звал `hide()` —
   * аппаратная кнопка пропадала у каждого, кто открывал скан впервые.
   */
  onBack: (() => void) | undefined;
  onAccept: () => void;
  onDecline: () => void;
}) {
  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Скан еды</h1>
      </header>
      <main className="food-scanner-screen__main">
        <section
          className="food-scanner-consent"
          aria-labelledby="food-consent-h2"
        >
          {canonical ? (
            <>
              <h2 id="food-consent-h2" className="food-scanner-consent__headline">
                {DISCLOSURE_HEADLINE}
              </h2>
              {/* Текст берётся из единственного источника, а не пишется здесь:
                  Gate A требует совпадения на всех поверхностях, а скопированный
                  абзац расходится с оригиналом молча. */}
              {DISCLOSURE_BODY.map((paragraph) => (
                <p className="food-scanner-consent__body" key={paragraph}>
                  {paragraph}
                </p>
              ))}
              <p
                className="food-scanner-consent__version"
                data-testid="food-diary-disclosure-version"
              >
                {FOOD_DIARY_DISCLOSURE_VERSION}
              </p>
            </>
          ) : (
            <>
              <h2 id="food-consent-h2" className="food-scanner-consent__headline">
                Можно показать тебе фото-скан?
              </h2>
              <p className="food-scanner-consent__body">
                Я возьму фото только чтобы узнать блюдо — посчитаю примерные
                калории и БЖУ. Удаляю фото сразу после распознавания,
                мастер и салон его не видят.
              </p>
            </>
          )}
          <p className="food-scanner-consent__body">
            Если ты не хочешь — это нормально. Можно вернуться на главную
            и продолжать без сканера.
          </p>
          <div className="food-scanner-consent__actions">
            <button
              type="button"
              className="btn-primary food-scanner-consent__accept"
              onClick={onAccept}
            >
              {canonical ? BUTTON_GRANT : "Хорошо, разрешаю"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={onDecline}
            >
              {canonical ? BUTTON_DECLINE : "Не сейчас"}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
