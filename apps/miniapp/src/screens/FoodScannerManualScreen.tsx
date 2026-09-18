/**
 * Запись еды текстом — Customer Food Diary, F8 (текстовая половина, DRF-2091).
 *
 * Route: `/customer/food-scanner/manual`. Вход — «Добавить приём» из
 * дневника (F5) и «Записать текстом» с дашборда. Фото — не здесь (D26 у
 * владельца): маршруты сканера остаются, но с этого экрана на них не ведёт.
 *
 * Та же тропа, что у текста в чате (F2 #1729 + #1823), теми же словами:
 *   1. поле «Что съела?» — «борщ 250»: граммы в конце фразы — порция по
 *      словам человека; без граммов — порция оценивается;
 *   2. `POST /food/estimate` — оценка БЕЗ записи;
 *   3. карточка «Я распознала так: …» — каждое предположение названо
 *      предположением: слова «примерно» и «оценка» на карточке обязательны
 *      (сторож в тесте, как у чата);
 *   4. «В дневник» — запись как показано (`corrected=false` →
 *      `text_estimated_confirmed`); «Поправить граммы» — новая оценка с
 *      названными граммами и запись с `corrected=true` (`text_user_corrected`);
 *   5. возврат в дневник: запись в списке — и есть подтверждение.
 *
 * Согласие дневника — из реестра (DRF-1963): без него сервер отвечает 403
 * `food_diary_consent_required`, и экран ведёт на гейт согласия (он живёт в
 * экране съёмки) с возвратом сюда. «Не смогли спросить» ≠ «согласия нет»:
 * сетевой сбой чтения на гейт не ведёт.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Snackbar } from "../components/Snackbar";
import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import {
  estimateFoodText,
  fetchConsentAt,
  logFoodText,
  type FoodTextEstimate,
} from "../lib/food-scanner";
import { backTo } from "../lib/screen-back";

export const DIARY_ROUTE = "/customer/food-scanner/diary";
export const MANUAL_ROUTE = "/customer/food-scanner/manual";
const CONSENT_GATE_ROUTE = "/customer/food-scanner/capture";

export const MANUAL_COPY = {
  title: "Записать текстом",
  whatField: "Что съела?",
  whatInputLabel: "Еда текстом",
  whatPlaceholder: "например, борщ 250",
  whatHint: "Можно с граммами в конце — «гречка с курицей 200». Без граммов порцию оценю.",
  estimate: "Оценить",
  estimating: "Считаю…",
  cardTitle: (dish: string) => `Я распознала так: ${dish}.`,
  portionEstimated: (g: number) => `Порция — примерно ${g} г, это оценка: граммов в сообщении не было.`,
  portionNamed: (g: number) => `Порция — ${g} г, по твоим словам.`,
  macros: (kcal: number, rest: string) => `Примерно ${kcal} ккал${rest} — оценка по справочнику блюд.`,
  confirmQuestion: "Записать в дневник?",
  toDiary: "В дневник",
  fixGrams: "Поправить граммы",
  gramsField: "Сколько граммов?",
  recalc: "Пересчитать",
  cancelGrams: "Отмена",
  saving: "Записываю…",
  emptyText: "Подскажи, что съела — пара слов.",
  badGrams: "Граммы — целое число от 1 до 5000.",
  notRecognized: "Не нашла такое блюдо в справочнике. Попробуй назвать иначе.",
  unavailable: "Сервис питания сейчас недоступен. Попробуй чуть позже.",
  saveFailed: "Не получилось записать. Попробуй ещё раз.",
  diaryOff: "Дневник питания пока недоступен.",
} as const;

type Card = {
  estimate: FoodTextEstimate;
  /** Граммы поправлены человеком на карточке — код происхождения меняется. */
  corrected: boolean;
  /** Один ключ на карточку: повтор запроса после потери ответа не пишет вторую запись. */
  idempotencyKey: string;
};

function mintKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Текст карточки — предположения названы предположениями (как в чате, §109 шаг 4). */
export function renderEstimateLines(estimate: FoodTextEstimate): string[] {
  const grams = Math.round(estimate.portion_g);
  const macros: string[] = [];
  for (const [label, value] of [
    ["Б", estimate.protein_g],
    ["Ж", estimate.fat_g],
    ["У", estimate.carbs_g],
  ] as Array<[string, number | null]>) {
    if (value !== null && value !== undefined) macros.push(`${label} ${Math.round(value)}`);
  }
  const rest = macros.length ? ` · ${macros.join(" · ")}` : "";
  return [
    MANUAL_COPY.cardTitle(estimate.matched_dish),
    estimate.portion_estimated ? MANUAL_COPY.portionEstimated(grams) : MANUAL_COPY.portionNamed(grams),
    MANUAL_COPY.macros(Math.round(estimate.kcal), rest),
  ];
}

function refusal(e: unknown): { slug: string; status: number } | null {
  if (!(e instanceof ApiError)) return null;
  return { slug: e.slug, status: e.status };
}

export function FoodScannerManualScreen() {
  const navigate = useNavigate();
  // Возврат — в дневник: сюда приходят из него и с дашборда, не из съёмки.
  const onBack = useScreenBack(backTo(DIARY_ROUTE));

  // DRF-2092 (F12) — «Записать из избранного»: экран избранного передаёт
  // блюдо и сохранённую порцию, и оценка зовётся сразу, без набора текста.
  // Тропа дальше та же: карточка → «В дневник» / «Поправить граммы».
  const location = useLocation();
  const fromSaved = (location.state as { fromSaved?: { dish_name?: unknown; portion_g?: unknown } } | null)
    ?.fromSaved;
  const savedDish = typeof fromSaved?.dish_name === "string" ? fromSaved.dish_name.trim() : "";
  const savedPortion =
    typeof fromSaved?.portion_g === "number" && Number.isFinite(fromSaved.portion_g)
      ? fromSaved.portion_g
      : undefined;

  const [text, setText] = useState(savedDish);
  const [card, setCard] = useState<Card | null>(null);
  const [gramsOpen, setGramsOpen] = useState(false);
  const [grams, setGrams] = useState("");
  const [busy, setBusy] = useState<"idle" | "estimating" | "saving">("idle");
  const [snack, setSnack] = useState<{ visible: boolean; message: string }>({ visible: false, message: "" });
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const toConsentGate = useCallback(() => {
    navigate(CONSENT_GATE_ROUTE, { replace: true, state: { returnTo: MANUAL_ROUTE } });
  }, [navigate]);

  // Из избранного: оценка с сохранённой порцией — один раз, на входе.
  // `corrected=false`: порцию человек назвал, когда сохранял блюдо; это не
  // поправка на карточке (§136 решается на карточке, не задним числом).
  const savedOnce = useRef(false);
  useEffect(() => {
    if (!savedDish || savedPortion === undefined || savedOnce.current) return;
    savedOnce.current = true;
    let cancelled = false;
    setBusy("estimating");
    estimateFoodText(savedDish, savedPortion)
      .then((est) => {
        if (cancelled || !alive.current) return;
        setCard({ estimate: est, corrected: false, idempotencyKey: mintKey() });
      })
      .catch((e) => {
        if (!cancelled && alive.current) handleRefusal(e, MANUAL_COPY.unavailable);
      })
      .finally(() => {
        if (!cancelled && alive.current) setBusy("idle");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedDish, savedPortion]);

  // Согласие спрашивается у сервера; отказ ЧТЕНИЯ на гейт не ведёт.
  useEffect(() => {
    let cancelled = false;
    fetchConsentAt()
      .then((at) => {
        if (cancelled || at !== null) return;
        toConsentGate();
      })
      .catch(() => {
        /* читать не удалось — экран остаётся на месте, гейт не зовём */
      });
    return () => {
      cancelled = true;
    };
  }, [toConsentGate]);

  const say = (message: string) => setSnack({ visible: true, message });

  const handleRefusal = (e: unknown, fallback: string): void => {
    const r = refusal(e);
    if (r?.slug === "food_diary_consent_required" || r?.slug === "consent_required") {
      toConsentGate();
      return;
    }
    if (r?.slug === "food_not_recognized") return say(MANUAL_COPY.notRecognized);
    if (r?.slug === "nutrition_disabled") return say(MANUAL_COPY.diaryOff);
    if (r?.slug === "nutrition_unavailable" || (r && r.status >= 500)) return say(MANUAL_COPY.unavailable);
    say(fallback);
  };

  const estimate = useCallback(
    async (portionG?: number) => {
      const trimmed = text.trim();
      if (!trimmed) return say(MANUAL_COPY.emptyText);
      setBusy("estimating");
      try {
        const est = await estimateFoodText(trimmed, portionG);
        if (!alive.current) return;
        setCard({
          estimate: est,
          corrected: portionG !== undefined,
          idempotencyKey: mintKey(),
        });
        setGramsOpen(false);
        setGrams("");
      } catch (e) {
        if (alive.current) handleRefusal(e, MANUAL_COPY.unavailable);
      } finally {
        if (alive.current) setBusy("idle");
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [text],
  );

  const recalc = () => {
    const n = Number(grams.trim());
    if (!/^\d{1,4}$/.test(grams.trim()) || n < 1 || n > 5000) return say(MANUAL_COPY.badGrams);
    void estimate(n);
  };

  const save = async () => {
    if (!card) return;
    setBusy("saving");
    try {
      await logFoodText({
        dish_name: card.estimate.matched_dish,
        portion_g: card.estimate.portion_g,
        corrected: card.corrected,
        idempotency_key: card.idempotencyKey,
      });
      if (!alive.current) return;
      // Возврат в дневник: запись в списке — и есть подтверждение.
      navigate(DIARY_ROUTE, { replace: true });
    } catch (e) {
      if (alive.current) handleRefusal(e, MANUAL_COPY.saveFailed);
    } finally {
      if (alive.current) setBusy("idle");
    }
  };

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button type="button" className="records-screen__back" aria-label="Назад" onClick={onBack}>
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M12 4l-6 6 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <h1 className="records-screen__title">{MANUAL_COPY.title}</h1>
      </header>

      <main className="food-scanner-screen__main">
        <section className="food-scanner-screen__section" aria-labelledby="food-manual-name-h3">
          <h3 id="food-manual-name-h3" className="food-scanner-screen__section-heading">
            {MANUAL_COPY.whatField}
          </h3>
          <input
            type="text"
            className="food-scanner-result__name-input"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setCard(null);
            }}
            placeholder={MANUAL_COPY.whatPlaceholder}
            aria-label={MANUAL_COPY.whatInputLabel}
            disabled={busy !== "idle"}
          />
          <p className="food-scanner-screen__hint">{MANUAL_COPY.whatHint}</p>
          {!card && (
            <div className="food-scanner-screen__cta-stack">
              <button type="button" className="btn-primary" disabled={busy !== "idle"} onClick={() => void estimate()}>
                {busy === "estimating" ? MANUAL_COPY.estimating : MANUAL_COPY.estimate}
              </button>
            </div>
          )}
        </section>

        {card && (
          <section className="food-scanner-screen__section food-text-card" aria-label="Я распознала так" data-testid="estimate-card">
            {renderEstimateLines(card.estimate).map((line) => (
              <p key={line} className="food-text-card__line">
                {line}
              </p>
            ))}
            {gramsOpen ? (
              <div className="food-text-card__grams">
                <label className="food-scanner-screen__section-heading" htmlFor="food-manual-grams">
                  {MANUAL_COPY.gramsField}
                </label>
                <input
                  id="food-manual-grams"
                  type="text"
                  inputMode="numeric"
                  className="food-scanner-result__name-input"
                  value={grams}
                  onChange={(e) => setGrams(e.target.value)}
                  placeholder="например, 300"
                  disabled={busy !== "idle"}
                />
                <div className="food-scanner-screen__cta-stack">
                  <button type="button" className="btn-primary" disabled={busy !== "idle"} onClick={recalc}>
                    {busy === "estimating" ? MANUAL_COPY.estimating : MANUAL_COPY.recalc}
                  </button>
                  <button type="button" className="btn-secondary" disabled={busy !== "idle"} onClick={() => setGramsOpen(false)}>
                    {MANUAL_COPY.cancelGrams}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className="food-text-card__line">{MANUAL_COPY.confirmQuestion}</p>
                <div className="food-scanner-screen__cta-stack">
                  <button type="button" className="btn-primary" disabled={busy !== "idle"} onClick={() => void save()}>
                    {busy === "saving" ? MANUAL_COPY.saving : MANUAL_COPY.toDiary}
                  </button>
                  <button type="button" className="btn-secondary" disabled={busy !== "idle"} onClick={() => setGramsOpen(true)}>
                    {MANUAL_COPY.fixGrams}
                  </button>
                </div>
              </>
            )}
          </section>
        )}
      </main>

      <Snackbar
        visible={snack.visible}
        message={snack.message}
        durationMs={3000}
        onTimeout={() => setSnack({ visible: false, message: "" })}
      />
    </div>
  );
}
