/**
 * /дневник surface — Customer Food Scanner daily log view.
 *
 * Route: `/customer/food-scanner/diary`
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` (R5 bonus
 * surface) + memory `project_variant_b_wellness_mvp` (food scanner
 * P0 for pilot 2026-07-15).
 *
 * Hides numeric values when `health_flags.eating_disorder === true`
 * (per spec §10 Appendix ED Mode).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Skeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  entriesLabel,
  formatTimeShort,
  type MealType,
} from "../lib/food-scanner";
import {
  correctFoodEntryGrams,
  deleteFoodEntry,
  loadDiaryToday,
  restoreFoodEntry,
  type DiaryToday,
  type FoodDiaryEntry,
  DIARY_CONSENT_REQUIRED_TEXT,
} from "../lib/customer-wellness";
import { ApiError } from "../lib/api";
import { saveMealFromEntry } from "../lib/saved-meals";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";
import { FAVORITES_COPY, FAVORITES_ROUTE, favoritesRefusalText } from "./FoodScannerFavoritesScreen";
import { WEEK_COPY, WEEK_ROUTE } from "./FoodScannerWeekScreen";

/**
 * ЧЕТЫРЕ состояния, и свести любые два нельзя — у каждого своя правда
 * и своя цена молчания:
 *
 * * `loading`     — ждём;
 * * `error`       — **ответ не пришёл**: сеть, таймаут, не-2xx;
 * * `unreadable`  — **ответ пришёл, но без записей**: сервер не смог
 *   прочитать питательную половину. Повтор осмыслен, но сообщение
 *   другое, и «сегодня ничего не записано» тут было бы ложью;
 * * `ready`       — записи (возможно, ноль штук — это ответ, а не сбой).
 *
 * До 08.09.2026 состояний было три, и `ready` с пустым списком был
 * неотличим от «данные не доехали»: заглушка возвращала нули и пустой
 * список одинаково в обоих случаях.
 */
type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "unreadable" }
  // DRF-1927 — нет согласия: дневник не читался, повтор ничего не даст.
  | { kind: "consent_required" }
  | {
      kind: "ready";
      day: Extract<DiaryToday, { state: "empty" | "entries" }>;
    };

const KNOWN_MEALS = ["breakfast", "lunch", "dinner", "snack"] as const;

/** Куда попадает приём пищи, которого экран ещё не знает. */
const OTHER_MEALS = "__other__";
type GroupKey = MealType | typeof OTHER_MEALS;

const MEAL_ORDER: ReadonlyArray<GroupKey> = [...KNOWN_MEALS, OTHER_MEALS];

const GROUP_ICON: Record<GroupKey, string> = {
  ...MEAL_TYPE_ICON,
  [OTHER_MEALS]: "🍽",
};
const GROUP_LABEL: Record<GroupKey, string> = {
  ...MEAL_TYPE_LABEL,
  [OTHER_MEALS]: "Другое",
};

export function FoodScannerDiaryScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом; адрес прежний, теперь объявленный.
  const onBack = useScreenBack(backTo("/customer/main"));
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const day = await loadDiaryToday();
      // «Ответ пришёл, записей в нём нет» — своё состояние, не ошибка
      // и не пустой день.
      setStatus(
        day.state === "unreadable"
          ? { kind: "unreadable" }
          : day.state === "consent_required"
            ? { kind: "consent_required" }
            : { kind: "ready", day },
      );
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // DRF-1838 — §109 шаг 7: сохранённую запись можно исправить или удалить.
  // Уведомление живёт ВНЕ списка: после каждого действия день перечитывается,
  // и «Вернуть» обязано пережить это перечитывание.
  const [notice, setNotice] = useState<EntryNotice | null>(null);
  // Одно действие за раз: двойной тап слал бы два запроса — второй «Удалить»
  // получал 404 и стирал «Вернуть», второй «Вернуть» говорил «записи нет»
  // про только что возвращённую запись. Ref — чтобы защита не ждала рендера.
  const busy = useRef(false);
  const [pending, setPending] = useState(false);

  const runEntryAction = useCallback(
    async (action: () => Promise<void>) => {
      if (busy.current) return;
      busy.current = true;
      setPending(true);
      try {
        await action();
      } catch (err) {
        setNotice({ text: entryErrorText(err) });
        // Неизвестный исход: изменение могло пройти — показать правду.
        if (err instanceof ApiError && err.slug === "ayla_uncertain") await load();
      } finally {
        busy.current = false;
        setPending(false);
      }
    },
    [load],
  );

  const onDelete = useCallback(
    (entry: FoodDiaryEntry) =>
      runEntryAction(async () => {
        await deleteFoodEntry(entry.id);
        setNotice({
          text: `Убрано: ${entry.dish_name}. Вернуть можно ${RESTORE_WINDOW_MINUTES} минут.`,
          undo: entry,
        });
        await load();
      }),
    [load, runEntryAction],
  );

  const onUndo = useCallback(
    (entry: FoodDiaryEntry) =>
      runEntryAction(async () => {
        // «Вернуть» снимается ДО запроса: второго шанса нажать его нет.
        setNotice((current) => (current ? { text: current.text } : current));
        const outcome = await restoreFoodEntry(entry.id);
        if (outcome === "restored") {
          setNotice({ text: `Вернула: ${entry.dish_name}.` });
          await load();
        } else if (outcome === "expired") {
          setNotice({
            text: `Уже не вернуть: прошло больше ${RESTORE_WINDOW_MINUTES} минут, запись удалена окончательно.`,
          });
        } else {
          setNotice({ text: ENTRY_GONE_TEXT });
        }
      }),
    [load, runEntryAction],
  );

  const onCorrect = useCallback(
    (entry: FoodDiaryEntry, grams: number) =>
      runEntryAction(async () => {
        await correctFoodEntryGrams(entry.id, grams);
        setNotice({ text: `Исправила: ${entry.dish_name}.` });
        await load();
      }),
    [load, runEntryAction],
  );

  // DRF-2092 (F12) — «В избранное»: шлётся id записи, снимок делает каталог
  // из своей записи. 201 и 200 сервера — разные фразы: «сохранила» и «уже в
  // избранном» для человека не одно и то же. День не перечитывается —
  // запись дневника не менялась.
  const onFavorite = useCallback(
    (entry: FoodDiaryEntry) => {
      if (busy.current) return;
      busy.current = true;
      setPending(true);
      void (async () => {
        try {
          const outcome = await saveMealFromEntry(entry.id);
          setNotice({
            text: outcome.created
              ? FAVORITES_COPY.savedNotice(entry.dish_name)
              : FAVORITES_COPY.alreadyNotice(entry.dish_name),
          });
        } catch (err) {
          if (err instanceof ApiError && err.slug === "food_diary_consent_required") {
            navigate("/customer/food-scanner/capture", {
              state: { returnTo: "/customer/food-scanner/diary" },
            });
            return;
          }
          setNotice({
            text:
              err instanceof ApiError && err.slug === "consent_required"
                ? DIARY_CONSENT_REQUIRED_TEXT
                : favoritesRefusalText(err),
          });
        } finally {
          busy.current = false;
          setPending(false);
        }
      })();
    },
    [navigate],
  );

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
        <h1 className="records-screen__title">Питание</h1>
      </header>

      <main className="food-scanner-screen__main">
        {/* Живая область есть всегда, меняется только текст: область,
            вставленная вместе с текстом, часть экранных дикторов пропускает. */}
        <div className="food-scanner-diary__notice-slot" aria-live="polite">
          {notice && (
            <div className="food-scanner-diary__notice">
              <span>{notice.text}</span>
              {notice.undo && (
                <button
                  type="button"
                  className="food-scanner-diary__notice-action"
                  disabled={pending}
                  onClick={() => {
                    if (notice.undo) void onUndo(notice.undo);
                  }}
                >
                  Вернуть
                </button>
              )}
            </div>
          )}
        </div>

        {status.kind === "loading" && (
          <div className="food-scanner-diary__skeleton" aria-hidden="true">
            <Skeleton width="40%" height="1.1em" />
            <div style={{ marginTop: "var(--s-3)" }}>
              <Skeleton width="90%" height="0.95em" />
            </div>
            <div style={{ marginTop: "var(--s-2)" }}>
              <Skeleton width="70%" height="0.95em" />
            </div>
          </div>
        )}

        {status.kind === "error" && (
          <StateError err={status.err} onRetry={load} />
        )}

        {status.kind === "unreadable" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>Не удалось загрузить дневник за сегодня.</p>
            <p className="food-scanner-diary__unreadable-hint">
              Записи не потерялись — их сейчас не удалось прочитать.
            </p>
            <button type="button" className="btn-secondary" onClick={load}>
              Попробовать снова
            </button>
          </div>
        )}

        {/* DRF-1927 — без согласия дневник не читался: не сбой и не пустой
            день, повтор ничего не даст, поэтому и кнопки повтора нет. */}
        {status.kind === "consent_required" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{DIARY_CONSENT_REQUIRED_TEXT}</p>
          </div>
        )}

        {status.kind === "ready" && (
          <DiaryReady
            day={status.day}
            // DRF-2091: «Добавить приём» ведёт на запись текстом — фото-половина
            // ждёт решение владельца (D26), обещать её кнопкой нельзя.
            onAddTap={() => navigate("/customer/food-scanner/manual")}
            onFavoritesTap={() => navigate(FAVORITES_ROUTE)}
            onWeekTap={() => navigate(WEEK_ROUTE)}
            onDelete={onDelete}
            onCorrect={onCorrect}
            onFavorite={onFavorite}
            pending={pending}
          />
        )}
      </main>
    </div>
  );
}

function DiaryReady({
  day,
  onAddTap,
  onFavoritesTap,
  onWeekTap,
  onDelete,
  onCorrect,
  onFavorite,
  pending,
}: {
  day: Extract<DiaryToday, { state: "empty" | "entries" }>;
  onAddTap: () => void;
  onFavoritesTap: () => void;
  onWeekTap: () => void;
  onDelete: (entry: FoodDiaryEntry) => Promise<void>;
  onCorrect: (entry: FoodDiaryEntry, grams: number) => Promise<void>;
  onFavorite: (entry: FoodDiaryEntry) => void;
  pending: boolean;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const entries = day.state === "entries" ? day.entries : [];
  const grouped = groupByMeal(entries);
  const totalCount = entries.length;
  // Признак приходит от источника; его отсутствие уже превращено в
  // «прятать» на уровне чтения (fail-closed, §10 Appendix ED Mode).
  const showNumbers = !day.hideNumbers;
  const { calories_eaten: eaten, calories_target: target, pfc } = day.today;
  // DRF-1839. Добавление через скан — экран под `guardProd`, в прод-сборке
  // он падает в момент использования (§33, DRF-1546 сняли его с главной по
  // той же причине). Работающий вход записи — чат: текстовый ввод DRF-1837
  // («гречка 200 г» → оценка → подтверждение). Кнопка скана остаётся только
  // в DEV, где заглушки живы.
  const scanEntryLive = import.meta.env.DEV;
  return (
    <>
      <p className="food-scanner-diary__caption">
        {totalCount === 0
          ? "Пока ничего не записано. Напиши Ayla в чате, что было, — например «гречка 200 г»: она посчитает и покажет, прежде чем записать."
          : `Сегодня — ${entriesLabel(totalCount)}.`}
      </p>

      {MEAL_ORDER.map((mt) => {
        const items = grouped[mt];
        if (!items || items.length === 0) return null;
        return (
          <section
            key={mt}
            className="food-scanner-diary__group"
            aria-labelledby={`food-diary-group-${mt}`}
          >
            <h2
              id={`food-diary-group-${mt}`}
              className="food-scanner-diary__group-heading"
            >
              <span aria-hidden="true">{GROUP_ICON[mt]}</span>{" "}
              {GROUP_LABEL[mt]}
            </h2>
            <ul className="food-scanner-diary__list">
              {items.map((entry) => (
                <li key={entry.id} className="food-scanner-diary__entry">
                  <div className="food-scanner-diary__entry-main">
                    <span className="food-scanner-diary__entry-time">
                      {formatTimeShort(entry.logged_at)}
                    </span>
                    <span className="food-scanner-diary__entry-dish">
                      {entry.dish_name}
                    </span>
                  </div>
                  {showNumbers && (
                    <span className="food-scanner-diary__entry-cal">
                      ~{entry.calories} ккал
                    </span>
                  )}
                  <div className="food-scanner-diary__entry-actions">
                    {isTextEntry(entry) && (
                      <button
                        type="button"
                        className="food-scanner-diary__entry-action"
                        aria-label={`Исправить граммы: ${entry.dish_name}`}
                        disabled={pending}
                        onClick={() => setEditing(entry.id)}
                      >
                        Граммы
                      </button>
                    )}
                    <button
                      type="button"
                      className="food-scanner-diary__entry-action"
                      aria-label={`В избранное: ${entry.dish_name}`}
                      disabled={pending}
                      onClick={() => onFavorite(entry)}
                    >
                      В избранное
                    </button>
                    <button
                      type="button"
                      className="food-scanner-diary__entry-action"
                      aria-label={`Удалить: ${entry.dish_name}`}
                      disabled={pending}
                      onClick={() => {
                        void onDelete(entry);
                      }}
                    >
                      Удалить
                    </button>
                  </div>
                  {editing === entry.id && (
                    <GramsForm
                      entry={entry}
                      onSave={(grams) => {
                        setEditing(null);
                        void onCorrect(entry, grams);
                      }}
                      onCancel={() => setEditing(null)}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      {totalCount > 0 && showNumbers && (
        <section
          className="food-scanner-diary__totals"
          aria-labelledby="food-diary-totals-h2"
        >
          <h2
            id="food-diary-totals-h2"
            className="food-scanner-screen__section-heading"
          >
            Сегодня
          </h2>
          {/* Цель рисуется, ТОЛЬКО когда она есть. Ключа нет — цели нет
              (анкету человек не проходил), и «/ 0 ккал» на её месте
              было бы чужим числом, выданным за его собственное (§65). */}
          {eaten !== undefined && (
            <p className="food-scanner-saved__total">
              {target !== undefined ? `${eaten} / ${target} ккал` : `${eaten} ккал`}
            </p>
          )}
          {/* БЖУ — строка целевая: живёт и гаснет вместе с целью, ровно
              как на дашборде. Считать его здесь не из чего и незачем:
              настоящее приходит с каждой записью. */}
          {pfc && (
            <p className="food-scanner-saved__macros">
              Б {pfc.protein_g}
              {pfc.protein_target_g !== undefined
                ? ` / ${pfc.protein_target_g}`
                : ""}{" "}
              · Ж {pfc.fat_g} · У {pfc.carbs_g} г
            </p>
          )}
        </section>
      )}

      {/* Строка диетолога (DRF-1897) — под итогами, тем же текстом, что в
          чате. Сервер присылает её только этому экрану и уже записал её
          в журнал как показанную; нет ключа — нет и абзаца. */}
      {day.today.coach_observation && (
        <p className="food-scanner-diary__observation">
          {day.today.coach_observation}
        </p>
      )}

      {scanEntryLive && (
        <div className="food-scanner-screen__cta-stack">
          <button
            type="button"
            className="btn-primary"
            onClick={onAddTap}
          >
            Добавить приём
          </button>
        </div>
      )}

      {/* DRF-2092 (F12) — избранное живёт на сервере; вход отсюда, не с
          дашборда: избранное растёт из записей дневника. */}
      <div className="food-scanner-screen__cta-stack">
        <button type="button" className="btn-secondary" onClick={onFavoritesTap}>
          {FAVORITES_COPY.openFromDiary}
        </button>
        {/* DRF-2099 — неделя: факт «N из 7 дней с записями», без напоминаний. */}
        <button type="button" className="btn-secondary" onClick={onWeekTap}>
          {WEEK_COPY.openFromDiary}
        </button>
      </div>
    </>
  );
}

/**
 * Разложить записи по приёмам пищи.
 *
 * `meal_type` приходит строкой, и у источника их сегодня ровно четыре
 * (`nutrition/models.py::FoodLog.MealType`). Пятый когда-нибудь
 * появится — и тогда запись обязана **остаться на экране**, а не
 * исчезнуть: тихий пропуск здесь означал бы, что экран решает, какую
 * из съеденных человеком тарелок он ему покажет. Незнакомый тип
 * попадает в «Другое» (§78: у каждого пропуска должно быть имя, а
 * лучший вид имени — отсутствие самого пропуска).
 */
function groupByMeal(
  entries: FoodDiaryEntry[],
): Partial<Record<GroupKey, FoodDiaryEntry[]>> {
  const out: Partial<Record<GroupKey, FoodDiaryEntry[]>> = {};
  for (const e of entries) {
    const key: GroupKey = (KNOWN_MEALS as readonly string[]).includes(e.meal_type)
      ? (e.meal_type as MealType)
      : OTHER_MEALS;
    const bucket = out[key] ?? [];
    bucket.push(e);
    out[key] = bucket;
  }
  return out;
}

// ─── DRF-1838: правка и удаление записи ─────────────────────────────────

/** Окно восстановления каталога (`food_log_edit_service.RESTORE_WINDOW_MINUTES`). */
const RESTORE_WINDOW_MINUTES = 15;
const GRAMS_MIN = 10;
const GRAMS_MAX = 2000;
const ENTRY_GONE_TEXT = "Этой записи уже нет в дневнике.";

type EntryNotice = { text: string; undo?: FoodDiaryEntry };

/**
 * «Граммы ÷ 100» верно только для записи, сделанной текстом: у фото-записи
 * порция считается от скана. Старые записи без происхождения — тоже нет.
 */
function isTextEntry(entry: FoodDiaryEntry): boolean {
  return (
    entry.entry_origin === "text_estimated_confirmed" ||
    entry.entry_origin === "text_user_corrected"
  );
}

/** Каждый отказ — своей фразой. «Не знаю, дошло ли» ≠ «ничего не изменилось». */
function entryErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.slug === "consent_required") {
      return DIARY_CONSENT_REQUIRED_TEXT;
    }
    if (err.slug === "ayla_uncertain") {
      return "Не знаю, дошло ли — обнови дневник, прежде чем повторять.";
    }
    if (err.slug === "water_managed") {
      return "Эту запись ведёт учёт воды — её убирает отмена стакана.";
    }
    if (err.slug === "not_found") return ENTRY_GONE_TEXT;
    if (err.slug === "ayla_bad_request") {
      return "Дневник не принял изменение — проверь запись и попробуй ещё раз.";
    }
  }
  return "Дневник сейчас не отвечает — ничего не изменилось. Попробуй позже.";
}

function GramsForm({
  entry,
  onSave,
  onCancel,
}: {
  entry: FoodDiaryEntry;
  onSave: (grams: number) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputId = `food-diary-grams-${entry.id}`;
  return (
    <form
      className="food-scanner-diary__grams"
      onSubmit={(event) => {
        event.preventDefault();
        const grams = Number(value.trim());
        if (!Number.isInteger(grams) || grams < GRAMS_MIN || grams > GRAMS_MAX) {
          setError(`Граммы — числом от ${GRAMS_MIN} до ${GRAMS_MAX}.`);
          return;
        }
        onSave(grams);
      }}
    >
      <label htmlFor={inputId} className="food-scanner-diary__grams-label">
        {`Сколько граммов было: ${entry.dish_name}`}
      </label>
      {/* Текстовое поле, а не type="number": границы проверяет код и
          называет ошибку словами, а не браузер молча. */}
      <input
        id={inputId}
        className="food-scanner-diary__grams-input"
        type="text"
        inputMode="numeric"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <button type="submit" className="btn-secondary">
        Сохранить
      </button>
      <button type="button" className="btn-secondary" onClick={onCancel}>
        Отмена
      </button>
      {error && (
        <p className="food-scanner-diary__grams-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
