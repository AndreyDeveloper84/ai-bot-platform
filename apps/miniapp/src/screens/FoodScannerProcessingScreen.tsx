/**
 * F2 Processing — Customer Food Scanner Phase B.
 *
 * Route: `/customer/food-scanner/processing` (router state carries
 * `{ photo: File, mealType: MealType }` from F1).
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §3 (F2 layout) +
 * §11 (a11y).
 *
 * # State machine
 *
 *   t=0           render Ayla line + pulsing dots
 *   t≥3000ms      show «Если занимает дольше…» + cancel button
 *   scan resolve  navigate F3 with ScanResponse + photo preview
 *   scan reject   navigate to F1 (FoodNotRecognized → manual entry
 *                 path), or surface error state inline
 *   user cancel   navigate back to F1 with photo preserved
 *   t≥10000ms     timeout — treat as NutritionUnavailableError
 *
 * Pulsing dot animation respects `prefers-reduced-motion` via CSS.
 *
 * DRF-2954 — вид по доске «Дневник» (кадры 4, 9), решения владельца 12.11:
 * слова и переходы прежние; списка шагов распознавания нет (п.11): экран не
 * знает, чем занят сервер.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  FoodNotRecognizedError,
  NutritionUnavailableError,
  PhotoBytesMissingError,
  PhotoTooLargeError,
  ScanBudgetExhaustedError,
  ScanDailyLimitError,
  StubNotWiredError,
  scanPhoto,
  type MealType,
} from "../lib/food-scanner";
import { RecordIcon } from "../components/records/RecordIcons";
import { RecordButton } from "../components/records/RecordsState";
import { ApiError } from "../lib/api";
import { useScreenBack } from "../hooks/useScreenBack";
import { backToOrigin } from "../lib/screen-back";

interface RouterState {
  photo?: File;
  mealType?: MealType;
  /** DRF-2349 — откуда вошли в поток; едет по всем его шагам. */
  returnTo?: string;
}

type ProcessingState =
  | { kind: "scanning" }
  | { kind: "showCancel" }
  | { kind: "error"; err: unknown };

const SHOW_CANCEL_AFTER_MS = 3000;
const TIMEOUT_MS = 10000;

export function FoodScannerProcessingScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом. Своей стрелки экран не рисует
  // (распознавание идёт секунды и его отменяют кнопкой «Отменить»), но
  // аппаратная кнопка MAX существует независимо от разметки — и без
  // объявления увела бы из приложения. Объявление обязательно и здесь.
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  // DRF-2349 — происхождение пришло из съёмки; возврат и все переходы
  // потока везут его дальше, иначе выход уйдёт на Главную.
  const onBack = useScreenBack(backToOrigin(location.state, "/customer/main"));
  const photo = state.photo;
  const mealType = state.mealType ?? "lunch";

  const [phase, setPhase] = useState<ProcessingState>({ kind: "scanning" });
  const abortRef = useRef<AbortController | null>(null);
  const cancelTimerRef = useRef<number | null>(null);
  const timeoutTimerRef = useRef<number | null>(null);
  // Адрес превью создаётся и освобождается В ОДНОМ эффекте (DRF-2399).
  //
  // Здесь был `useMemo`, и это та же половина дефекта, что вычищена на
  // экране результата: `StrictMode` зовёт фабрику дважды, оставляет второе
  // значение, а очистка отзывает именно его — адрес в `src` мёртв, пока
  // экран смонтирован, а первый утекает насовсем. Форма ниже делает это
  // невозможным: одна живая подписка — один адрес, и отзывает его она же.
  //
  // Цена названа: превью появляется на один коммит позже.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!photo) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    // Preserve photo + mealType so the user does not re-pick.
    navigate("/customer/food-scanner/capture", {
      replace: true,
      state: { photo, mealType, returnTo: state.returnTo },
    });
  }, [navigate, photo, mealType, state.returnTo]);

  useEffect(() => {
    // Guard — if user landed here without a photo (deep link refresh),
    // bounce back to F1 so we never call scan with an empty payload.
    if (!photo) {
      navigate("/customer/food-scanner/capture", { replace: true });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;

    cancelTimerRef.current = window.setTimeout(() => {
      if (controller.signal.aborted) return;
      setPhase((p) => (p.kind === "scanning" ? { kind: "showCancel" } : p));
    }, SHOW_CANCEL_AFTER_MS);

    timeoutTimerRef.current = window.setTimeout(() => {
      // Guard against setState-after-unmount: if effect cleanup
      // already ran, the controller is aborted; bail before touching
      // state (adversarial CR P7).
      if (controller.signal.aborted) return;
      controller.abort();
      setPhase({ kind: "error", err: new NutritionUnavailableError() });
    }, TIMEOUT_MS);

    (async () => {
      try {
        const result = await scanPhoto(photo, { signal: controller.signal });
        if (controller.signal.aborted) return;
        navigate("/customer/food-scanner/result", {
          replace: true,
          // `previewUrl` НЕ передаётся (DRF-2399): адресом владеет тот,
          // кто рисует. Экран результата получает файл и делает свой —
          // иначе адрес переживал бы владельца и умирал у чужого экрана.
          state: { result, photo, mealType, returnTo: state.returnTo },
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        // AbortError surfaces through the same rejection path; treat
        // as silent cancel rather than rendering an error screen.
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setPhase({ kind: "error", err });
      }
    })();

    return () => {
      controller.abort();
      if (cancelTimerRef.current !== null)
        window.clearTimeout(cancelTimerRef.current);
      if (timeoutTimerRef.current !== null)
        window.clearTimeout(timeoutTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- снимок обрабатывается один раз за вход на экран; все пять значений приходят навигацией и на монтировании постоянны
  }, []);

  // ── error branches per spec §7 ─────────────────────────────────────
  if (phase.kind === "error") {
    return (
      <ScanErrorScreen
        returnTo={state.returnTo}
        onBack={onBack}
        err={phase.err}
        photo={photo ?? null}
        mealType={mealType}
      />
    );
  }

  return (
    <div className="cr-screen cr-screen--nested diary-screen">
      <header className="cr-topbar diary-topbar diary-topbar--bare">
        {/* Стрелки нет: распознавание идёт секунды и отменяется кнопкой. */}
        <h1 className="diary-topbar__title">Распознавание</h1>
      </header>
      <main className="diary-main diary-processing" role="status" aria-live="polite">
        {previewUrl && (
          <div className="diary-processing__preview">
            <img src={previewUrl} alt="Фото блюда" className="diary-processing__photo" />
          </div>
        )}
        <p className="diary-processing__line">Узнаю что на фото</p>
        <div className="diary-processing__dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        {phase.kind === "showCancel" && (
          <>
            <p className="diary-processing__hint">
              Если занимает дольше обычного, можно отменить — фото останется на
              месте.
            </p>
            <RecordButton variant="soft" onClick={cancel}>
              Отменить
            </RecordButton>
          </>
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline error screen — three branches per spec §7.
// ---------------------------------------------------------------------------

function ScanErrorScreen({
  onBack,
  err,
  photo,
  mealType,
  returnTo,
}: {
  /**
   * Возврат приходит готовым от экрана (DRF-1493): экран ошибки
   * живёт внутри `FoodScannerProcessingScreen`, который объявление уже
   * сделал. Свой `useScreenBack` здесь давал вторую подписку на
   * мосту MAX — два перехода на одно нажатие.
   */
  onBack: (() => void) | undefined;
  err: unknown;
  /**
   * DRF-2349 — происхождение приходит свойством по той же причине, что и
   * возврат: экран ошибки живёт внутри потока, а состояние маршрута знает
   * только его хозяин.
   */
  returnTo: string | undefined;
  photo: File | null;
  mealType: MealType;
}) {
  const navigate = useNavigate();
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => {
    titleRef.current?.focus();
  }, []);
  const isNotRecognized = err instanceof FoodNotRecognizedError;
  const isPhotoFailed = err instanceof PhotoBytesMissingError;
  // Ручки `/api/v1/customer/food/{scan,log,daily}` на боевом контуре
  // отвечают 404, и `food-scanner.ts::guardProd` вне DEV бросает
  // `StubNotWiredError` (тот же признак, что снял вход с главной,
  // DRF-1546). Это НЕ сбой сети и НЕ перегрузка: через минуту ничего не
  // изменится, потому что менять нечего — ручки не существует. Текст
  // «сервис временно недоступен, попробуй через минуту» врал о природе
  // отказа и звал человека возвращаться.
  const isNotWired = err instanceof StubNotWiredError;
  // DRF-2098 — два отказа самого прокси: 413 (лимит один на бота, тот же,
  // что у фото в чате) и отказ ворот дневника в полёте (согласие отозвано
  // между экраном и снимком) — тот же экран согласия, что до снимка.
  const isTooLarge = err instanceof PhotoTooLargeError;
  const isConsentGate =
    err instanceof ApiError &&
    (err.slug === "food_diary_consent_required" ||
      err.slug === "consent_required");
  // DRF-2109 — фото выключено флагом (тот же предикат, что у чата): это не
  // сбой и не «через минуту» — писать текстом работает, туда и ведём.
  const isPhotoOff =
    err instanceof ApiError && err.slug === "photo_scan_disabled";
  // DRF-2195 — два штатных отказа по бюджету распознавания. Каталог работает
  // и отвечает осознанно: «через минуту» здесь было бы ложью о природе отказа
  // (счёт снимется в полночь), а «Переснять» — ложью действием: второй снимок
  // упрётся в тот же счётчик. Рабочая дорога рядом — записать словами.
  // Заголовок и тело лежат парой в одном месте: у отказа по бюджету их два,
  // и регистрировать новый вид отказа в двух разных тернарных лестницах —
  // как раз та дорога, на которой 429 когда-то и потерялся.
  const budget =
    err instanceof ScanDailyLimitError
      ? {
          headline: "Сегодня фото не распознаю",
          body: "Сегодня фото больше не распознаю — напиши словами, что было.",
        }
      : err instanceof ScanBudgetExhaustedError
        ? {
            headline: "Распознавание фото недоступно",
            body: "Распознавание фото сейчас недоступно — напиши словами.",
          }
        : null;
  const isBudget = budget !== null;
  const headline = budget
    ? budget.headline
    : isNotRecognized
      ? "Не разобралась"
      : isPhotoFailed
        ? "Не получилось загрузить"
        : isTooLarge
          ? "Фото слишком большое"
          : isConsentGate
            ? "Нужно разрешение"
            : isPhotoOff
              ? "Фото пока не принимаю"
              : isNotWired
                ? "Пока не подключено"
                : "Сервис недоступен";
  const body = budget
    ? budget.body
    : isNotRecognized
      ? "Фото немного сложное — не разобралась. Можно переснять поближе или просто написать, что было."
      : isPhotoFailed
        ? "Фото пришло, но скачать не получилось — пришли ещё раз, пожалуйста."
        : isTooLarge
          ? "Такое фото не пройдёт — попробуй снять ещё раз или выбрать снимок поменьше."
          : isConsentGate
            ? "Чтобы распознавать еду по фото, нужно разрешение на дневник питания — вернись к сканеру и дай его."
            : isPhotoOff
              ? "Распознавание по фото сейчас выключено. Напиши, что было и сколько граммов, — посчитаю и покажу, прежде чем записать."
              : isNotWired
                ? "Распознавание еды по фото в приложении ещё не работает. Дневник питания сейчас ведёт Ayla в чате."
                : "Сервис распознавания временно недоступен. Попробуй через минуту.";
  // Значок различает природу отказа, как состояния дневника (часть 1):
  // нет разрешения — замок; выключено, исчерпано, не подключено — «i»; сбой —
  // красный знак. Иначе «нет согласия», «выключено» и «сбой» сливаются.
  const badge = isConsentGate
    ? { icon: "lock" as const, tone: "info" }
    : isPhotoOff || isBudget || isNotWired
      ? { icon: "info" as const, tone: "info" }
      : { icon: "alert" as const, tone: "danger" };

  // Кнопки — прежний набор и прежние условия; вид — столбцом (кадр 9).
  // Главная — та, что ведёт дальше по рабочей дороге.
  return (
    <div className="cr-screen cr-screen--nested diary-screen">
      <header className="cr-topbar diary-topbar">
        <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
          <RecordIcon name="back" size={24} />
        </button>
      </header>
      <main className="diary-main diary-scan-error">
        <span
          className={
            badge.tone === "danger"
              ? "cr-state__badge cr-state__badge--danger diary-scan-error__badge"
              : "cr-state__badge cr-state__badge--info diary-scan-error__badge"
          }
        >
          <RecordIcon name={badge.icon} size={40} />
        </span>
        {/* Заголовок ошибки — заголовком экрана: шапка его больше не несёт.
            Экран подменяет распознавание на месте — фокус уводится сюда. */}
        <h1 ref={titleRef} tabIndex={-1} className="diary-scan-error__title">
          {headline}
        </h1>
        <p className="diary-scan-error__body" role="alert">
          {body}
        </p>
        <div className="cr-actions diary-actions">
          {/* Когда ручек нет, «Переснять» и «Написать вручную» ведут в ту
              же стену: `logMeal` закрыт тем же `guardProd`. Предлагать их
              значило бы врать второй раз, уже действием. */}
          {!isPhotoFailed && !isNotWired && !isPhotoOff && !isBudget && (
            <RecordButton
              variant="primary"
              onClick={() =>
                navigate("/customer/food-scanner/capture", {
                  replace: true,
                  state: { photo, mealType, returnTo },
                })
              }
            >
              Переснять
            </RecordButton>
          )}
          {(isPhotoFailed || isTooLarge || isConsentGate) && (
            <RecordButton
              variant="primary"
              onClick={() => navigate("/customer/food-scanner/capture", { replace: true })}
            >
              {isConsentGate ? "К сканеру" : "Сделать заново"}
            </RecordButton>
          )}
          {!isPhotoFailed && !isNotWired && !isTooLarge && !isConsentGate && (
            <RecordButton
              variant={isPhotoOff || isBudget ? "primary" : "soft"}
              onClick={() =>
                navigate("/customer/food-scanner/manual", {
                  state: { mealType, returnTo },
                })
              }
            >
              Написать вручную
            </RecordButton>
          )}
          <RecordButton variant="text" onClick={onBack}>
            Назад на главную
          </RecordButton>
        </div>
      </main>
    </div>
  );
}
