/**
 * Утверждения экрана о выполненном действии и их доказательства (DRF-2347).
 *
 * Сторож класса «сообщает о сделанном» (DRF-2341) держится на признаке
 * `claims_done` в ответах бота. В Mini App утверждение — это текст, который
 * человек прочитал на экране; носителя признака там нет, и серверный сторож
 * эту поверхность не видит вовсе.
 *
 * ## Правило — не «ответ прочитан», а «утверждение сверено с прочитанным»
 *
 * Первая формулировка правила была «за утверждением должен стоять прочитанный
 * ответ». DRF-2346 показал, что этого мало: экран запросил отмену, **прочитал**
 * `booking.status`, увидел `cancel_requested` — и сказал «Запись отменена».
 * Ответ был, он был прочитан, и он говорил обратное. Поэтому здесь сверяется
 * не факт чтения, а **значение**: исход, который экран утверждает, обязан
 * входить в список значений, при которых он верен.
 *
 * ## Почему доказательство нельзя подделать
 *
 * Доказательство — это сам объект, который вернул клиент API. Клиент ставит на
 * него метку (:func:`markRead`) скрытым символом; символ из модуля не
 * вывозится, поэтому поставить метку снаружи нельзя ни в рантайме, ни по типам.
 * Литерал `{ status: "cancelled" }` в поле доказательства не пройдёт проверку
 * типов, а собранный на месте объект — проверку метки. «Мы позвали ручку» и
 * «мы решили, что получилось» доказательствами не считаются по построению.
 *
 * ## Fail-closed
 *
 * Исход, которого нет в :data:`OUTCOMES`, — **красный**, а не «наверное можно».
 * Таблица ведётся руками, и однажды кто-то добавит источнику новый статус;
 * незнакомое значение обязано останавливать, а не проскакивать.
 *
 * ## Предел — назван заранее
 *
 * - Сообщения мимо примитива вывода этот механизм **не видит**. Размер слепого
 *   пятна измерен и зафиксирован числом: `claims-debt.ts`.
 * - Ложь источника (2xx без действия) недоказуема здесь так же, как в боте:
 *   мы доказываем, что источник **ответил** так, а не что он **сделал**.
 * - Утверждения о чужом будущем действии («администратор свяжется») не
 *   доказываются никаким чтением — у них признак бота, не эта таблица.
 * - Текст, пришедший с сервера, этим механизмом не проверяется: правдивость
 *   такого утверждения переезжает на серверную сторону, где действует признак
 *   бота (`claims_done`, DRF-2341). Это не дыра — это другой сторож.
 * - Верность самой таблицы соответствий не доказывается ничем: неверная строка
 *   сделает сторожа слепым ровно там, где ошиблись.
 */

/** Метка прочитанного. Символ наружу не вывозится — в этом вся защита. */
const PROOF = Symbol("claims.proof");

let readSeq = 0;

export interface ProofMeta {
  /** Откуда прочитано — путь ручки, как его звал клиент. */
  readonly source: string;
  /** Код ответа источника. */
  readonly status: number;
  /** Номер чтения: растёт монотонно, отличает свежее от прошлой попытки. */
  readonly seq: number;
}

/** Значение, прочитанное у источника через клиент API. */
export type Read<T> = T & { readonly [PROOF]: ProofMeta };

/**
 * Поставить метку на тело ответа. Зовётся ТОЛЬКО из обёрток клиента.
 *
 * Метка неперечислимая: она не попадёт ни в `JSON.stringify`, ни в снимки
 * тестов, ни в сравнение объектов — тело ответа остаётся тем же телом.
 */
export function markRead<T>(data: T, meta: Omit<ProofMeta, "seq">): Read<T> {
  if (data === null || typeof data !== "object") return data as Read<T>;
  Object.defineProperty(data, PROOF, {
    value: { ...meta, seq: ++readSeq },
    enumerable: false,
    configurable: true,
  });
  return data as Read<T>;
}

export function proofOf(value: unknown): ProofMeta | null {
  if (value === null || typeof value !== "object") return null;
  const meta = (value as Record<symbol, unknown>)[PROOF];
  return (meta as ProofMeta) ?? null;
}

/**
 * Исход, который экран вправе утверждать, и значения, при которых он верен.
 *
 * `field` — поле прочитанного объекта; `values` — значения этого поля, при
 * которых утверждение правда. Разделение «запрошено» и «сделано» — не
 * придирка: в DRF-2346 весь дефект ровно в этой паре.
 */
export const OUTCOMES = {
  booking_cancelled: { field: "status", values: ["cancelled"] },
  booking_cancel_requested: { field: "status", values: ["cancel_requested"] },
  booking_rescheduled: { field: "status", values: ["rescheduled", "confirmed"] },
  profile_published: { field: "profile_status", values: ["active", "pending"] },
  memory_forget_accepted: { field: "status", values: ["deletion_pending"] },
} as const;

export type Outcome = keyof typeof OUTCOMES;

export interface Claim<T> {
  /** Что именно экран утверждает выполненным. */
  readonly outcome: Outcome;
  /** Прочитанное у источника значение, из которого утверждение выведено. */
  readonly from: Read<T>;
  /**
   * Необязательно: номер чтения, старее которого доказательство не годится.
   * Держит случай «показали исход прошлого нажатия».
   */
  readonly after?: number;
}

export type ClaimVerdict =
  | { ok: true; meta: ProofMeta }
  | { ok: false; reason: string };

/**
 * Проверить утверждение: метка на месте, исход известен, значение его
 * подтверждает, доказательство не из прошлой попытки.
 */
export function verifyClaim<T>(claim: Claim<T>): ClaimVerdict {
  const rule = OUTCOMES[claim.outcome as Outcome] as
    | { field: string; values: readonly string[] }
    | undefined;
  if (!rule) {
    // Fail-closed: незнакомый исход останавливает, а не проскакивает.
    return { ok: false, reason: `исход «${String(claim.outcome)}» не описан в OUTCOMES` };
  }

  const meta = proofOf(claim.from);
  if (!meta) {
    return {
      ok: false,
      reason: "доказательство без метки: значение не прочитано клиентом API",
    };
  }

  if (claim.after !== undefined && meta.seq <= claim.after) {
    return {
      ok: false,
      reason: `доказательство из прошлой попытки (чтение ${meta.seq} ≤ ${claim.after})`,
    };
  }

  const actual = (claim.from as Record<string, unknown>)[rule.field];
  if (typeof actual !== "string" || !rule.values.includes(actual)) {
    return {
      ok: false,
      reason:
        `утверждение «${claim.outcome}» не подтверждено прочитанным: ` +
        `${rule.field}=${JSON.stringify(actual)}, ожидалось одно из ` +
        `${JSON.stringify(rule.values)}`,
    };
  }

  return { ok: true, meta };
}

/**
 * Проверка для места вывода: в отладочной и тестовой сборке — бросает,
 * в боевой — молчит и возвращает вердикт.
 *
 * Падать перед человеком из-за сторожа нельзя: он проверяет наши слова, а не
 * его действия. Но и молчать в тестах нельзя — там это и ловится.
 */
export function assertClaim<T>(claim: Claim<T>): ClaimVerdict {
  const verdict = verifyClaim(claim);
  if (!verdict.ok && import.meta.env?.MODE !== "production") {
    throw new Error(`claims: ${verdict.reason}`);
  }
  return verdict;
}
