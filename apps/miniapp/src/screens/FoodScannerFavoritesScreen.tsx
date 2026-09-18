/**
 * Избранные блюда — Customer Food Diary, F12 (DRF-2092).
 *
 * Route: `/customer/food-scanner/favorites`. Вход — «Избранное» из дневника
 * (F5). Источник — сервер (`customer/saved-meals` → каталог): избранное
 * переживает переустановку, на устройстве ничего не хранится.
 *
 * Что здесь есть:
 *   - список: блюдо, порция, ккал — числа под тем же ED-признаком, что у
 *     дневника (`nutrition_numbers_hidden` из `wellness/today`; отсутствие
 *     признака ПРЯЧЕТ числа — fail-closed, §10 Appendix ED Mode);
 *   - «Удалить» — строка уходит после ответа сервера, не раньше;
 *   - «Записать» — на экран текста той же тропой, что F8: `fromSaved`
 *     несёт блюдо и сохранённую порцию → оценка → карточка «Я распознала
 *     так» → подтверждение → запись. Второй тропы записи не заводится.
 *
 * Отказы — по имени слага, как у соседей: `nutrition_disabled` — дневник
 * недоступен; `consent_required` — гейт согласия (живёт в экране съёмки) с
 * возвратом сюда; `ayla_unavailable` / `ayla_uncertain` / 5xx — фраза и
 * «Повторить». Пустой список — своё состояние, не ошибка.
 *
 * Чего здесь нет: «Добавить в избранное» словами — избранное растёт из
 * записей дневника («В избранное» на записи), не из свободного ввода.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import { getWellnessToday } from "../lib/customer-wellness";
import { deleteSavedMeal, listSavedMeals, type SavedMeal } from "../lib/saved-meals";
import { backTo } from "../lib/screen-back";
import { DIARY_ROUTE, MANUAL_ROUTE } from "./FoodScannerManualScreen";

export const FAVORITES_ROUTE = "/customer/food-scanner/favorites";
const CONSENT_GATE_ROUTE = "/customer/food-scanner/capture";

export const FAVORITES_COPY = {
  title: "Избранное",
  openFromDiary: "Избранное",
  emptyTitle: "Избранного пока нет.",
  emptyHint: "Нажми «В избранное» у записи в дневнике — блюдо и порция сохранятся здесь.",
  record: "Записать",
  remove: "Удалить",
  retry: "Повторить",
  loading: "Загружаю…",
  portion: (g: number) => `${Math.round(g)} г`,
  kcal: (kcal: number) => `~${Math.round(kcal)} ккал`,
  diaryOff: "Дневник питания пока недоступен.",
  unavailable: "Избранное сейчас недоступно — сервис питания не отвечает. Попробуй чуть позже.",
  uncertain: "Не знаю, дошло ли — обнови список, прежде чем повторять.",
  savedNotice: (dish: string) => `Сохранила в избранное: ${dish}.`,
  alreadyNotice: (dish: string) => `${dish} уже в избранном.`,
  gone: "Этой строки уже нет в избранном.",
} as const;

/** Каждый отказ — своей фразой; «не знаю, дошло ли» ≠ «ничего не изменилось». */
export function favoritesRefusalText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.slug === "nutrition_disabled") return FAVORITES_COPY.diaryOff;
    if (err.slug === "ayla_uncertain") return FAVORITES_COPY.uncertain;
    if (err.slug === "not_found") return FAVORITES_COPY.gone;
  }
  return FAVORITES_COPY.unavailable;
}

type Status =
  | { kind: "loading" }
  | { kind: "ready"; items: SavedMeal[] }
  | { kind: "diary_off" }
  | { kind: "error"; text: string };

export function FoodScannerFavoritesScreen() {
  const navigate = useNavigate();
  const onBack = useScreenBack(backTo(DIARY_ROUTE));

  const [status, setStatus] = useState<Status>({ kind: "loading" });
  // Признак прячет числа, пока источник не сказал обратное (fail-closed).
  const [hideNumbers, setHideNumbers] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const busy = useRef(false);
  const [pending, setPending] = useState(false);

  const toConsentGate = useCallback(() => {
    navigate(CONSENT_GATE_ROUTE, { replace: true, state: { returnTo: FAVORITES_ROUTE } });
  }, [navigate]);

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    setNotice(null);
    try {
      const [items, today] = await Promise.all([
        listSavedMeals(),
        getWellnessToday().catch(() => null),
      ]);
      // «Не смогли спросить» про ED-признак — числа остаются спрятанными.
      setHideNumbers(today?.nutrition_numbers_hidden !== false);
      setStatus({ kind: "ready", items });
    } catch (err) {
      if (err instanceof ApiError && err.slug === "consent_required") {
        toConsentGate();
        return;
      }
      if (err instanceof ApiError && err.slug === "nutrition_disabled") {
        setStatus({ kind: "diary_off" });
        return;
      }
      setStatus({ kind: "error", text: favoritesRefusalText(err) });
    }
  }, [toConsentGate]);

  useEffect(() => {
    void load();
  }, [load]);

  const onRemove = useCallback(
    async (meal: SavedMeal) => {
      if (busy.current) return;
      busy.current = true;
      setPending(true);
      setNotice(null);
      try {
        await deleteSavedMeal(meal.id);
        setStatus((s) =>
          s.kind === "ready" ? { kind: "ready", items: s.items.filter((m) => m.id !== meal.id) } : s,
        );
      } catch (err) {
        if (err instanceof ApiError && err.slug === "consent_required") {
          toConsentGate();
          return;
        }
        setNotice(favoritesRefusalText(err));
        // Строки уже нет или исход неизвестен — показать правду, а не догадку.
        if (err instanceof ApiError && (err.slug === "not_found" || err.slug === "ayla_uncertain")) {
          await load();
        }
      } finally {
        busy.current = false;
        setPending(false);
      }
    },
    [load, toConsentGate],
  );

  const onRecord = useCallback(
    (meal: SavedMeal) => {
      navigate(MANUAL_ROUTE, {
        state: { fromSaved: { dish_name: meal.dish_name, portion_g: meal.portion_g } },
      });
    },
    [navigate],
  );

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button type="button" className="records-screen__back" aria-label="Назад" onClick={onBack}>
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
        <h1 className="records-screen__title">{FAVORITES_COPY.title}</h1>
      </header>

      <main className="food-scanner-screen__main">
        <div className="food-scanner-diary__notice-slot" aria-live="polite">
          {notice && (
            <div className="food-scanner-diary__notice">
              <span>{notice}</span>
            </div>
          )}
        </div>

        {status.kind === "loading" && (
          <p className="food-scanner-diary__caption" role="status">
            {FAVORITES_COPY.loading}
          </p>
        )}

        {status.kind === "diary_off" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{FAVORITES_COPY.diaryOff}</p>
          </div>
        )}

        {status.kind === "error" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{status.text}</p>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              {FAVORITES_COPY.retry}
            </button>
          </div>
        )}

        {status.kind === "ready" && status.items.length === 0 && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{FAVORITES_COPY.emptyTitle}</p>
            <p className="food-scanner-diary__unreadable-hint">{FAVORITES_COPY.emptyHint}</p>
          </div>
        )}

        {status.kind === "ready" && status.items.length > 0 && (
          <ul className="food-scanner-diary__list">
            {status.items.map((meal) => (
              <li key={meal.id} className="food-scanner-diary__entry">
                <div className="food-scanner-diary__entry-main">
                  <span className="food-scanner-diary__entry-dish">{meal.dish_name}</span>
                  <span className="food-scanner-diary__entry-time">
                    {FAVORITES_COPY.portion(meal.portion_g)}
                  </span>
                </div>
                {!hideNumbers && (
                  <span className="food-scanner-diary__entry-cal">
                    {FAVORITES_COPY.kcal(meal.calories)}
                  </span>
                )}
                <div className="food-scanner-diary__entry-actions">
                  <button
                    type="button"
                    className="food-scanner-diary__entry-action"
                    aria-label={`${FAVORITES_COPY.record}: ${meal.dish_name}`}
                    disabled={pending}
                    onClick={() => onRecord(meal)}
                  >
                    {FAVORITES_COPY.record}
                  </button>
                  <button
                    type="button"
                    className="food-scanner-diary__entry-action"
                    aria-label={`${FAVORITES_COPY.remove}: ${meal.dish_name}`}
                    disabled={pending}
                    onClick={() => {
                      void onRemove(meal);
                    }}
                  >
                    {FAVORITES_COPY.remove}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
