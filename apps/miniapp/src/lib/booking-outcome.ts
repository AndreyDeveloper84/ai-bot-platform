/**
 * Слова исходов записи и recovery-состояний для КЛИЕНТА (DRF-2178, Э-3).
 *
 * Этап 3 из 4, кадры 4–6 макета DRF-1320 и его блок recovery.
 *
 * ## Машина — из М-3, слова — с макета
 *
 * `booking-draft.ts` даёт `SubmitOutcome` и `outcomeKeepsDraft` — это
 * машина, и она переиспользуется целиком. А `SUBMIT_OUTCOME_COPY` писан
 * для ПЕРСОНАЛА: «выберите», «Обратитесь к владельцу салона», «Клиент и
 * услуга сохранены». Показать это человеку значило бы заговорить с ним
 * голосом админки — поэтому здесь свой словарь, с макета, и он заведён
 * РЯДОМ, а не вместо: мастерская форма говорит с персоналом по-прежнему.
 *
 * ## Состояний девять, а не восемь
 *
 * «Это время только что стало недоступно» (при выборе) и «Это время уже
 * заняли» (при создании) — разные моменты. Слова разные, действие одно.
 *
 * ## Текст — функция, а не строка
 *
 * Два состояния называют специалиста по имени. Единая форма `text(ctx)`
 * не даёт однажды показать человеку сырой шаблон с фигурными скобками, а
 * без имени текст остаётся связным — «Пока нет подходящего времени».
 *
 * ## Повтор — это повтор
 *
 * «Проверить статус» шлёт ТОТ ЖЕ запрос. Ключ идемпотентности собирает
 * сервер из (человек, мастер, услуга, время, оплата)
 * (`miniapp_api/views.py`), клиент его не выдумывает и не хранит. Значит
 * повторный запрос вернёт ту же запись, а не создаст вторую — принцип
 * макета «Дублирование записи невозможно» держится по построению.
 */
import { outcomeKeepsDraft, type SubmitOutcome } from "./booking-draft";

/**
 * Кадр 4 «Проверь запись» — действие на каждой строке, дословно с макета.
 *
 * Возврат «назад» не то же самое: он возвращает на предыдущий шаг, а
 * человеку нужно поправить конкретную строку — и знать заранее, какую.
 */
export const CHANGE_SERVICE = "Изменить услугу";
export const CHANGE_PROVIDER = "Изменить специалиста";
export const CHANGE_TIME = "Изменить время";

/**
 * Кадр 5 «Создаю запись…» — дословно с макета.
 *
 * До этого этапа состояние жило подписью на кнопке («Записываю…»), и
 * человек оставался на форме, поля которой уже ничего не решают.
 *
 * `CREATING_ALSO_IN_CHAT` — ПРАВКА 4 макета: сказать, что результат
 * придёт и в чат. Это не украшение: человек, не уверенный, прошла ли
 * запись, жмёт ещё раз, и именно так рождаются дубли.
 */
export const CREATING_HEAD = "Создаю запись…";
export const CREATING_HINT = "Обычно это занимает несколько секунд.";
export const CREATING_ALSO_IN_CHAT = "Результат также появится в чате Ayla.";

export interface OutcomeCopy {
  readonly text: string;
  /** Сохранять ли введённое — правило М-3, не второе такое же. */
  readonly keepsDraft: boolean;
}

/** Слова исходов создания записи — клиенту, на «ты». */
export const CUSTOMER_OUTCOME: Record<SubmitOutcome, OutcomeCopy> = {
  committed: { text: "Запись подтверждена.", keepsDraft: outcomeKeepsDraft("committed") },
  conflict: {
    text: "Это время уже заняли — выбери другое. Услуга и специалист сохранены.",
    keepsDraft: outcomeKeepsDraft("conflict"),
  },
  blocked: {
    text: "Записаться к этому специалисту сейчас нельзя. Посмотрим другого?",
    keepsDraft: outcomeKeepsDraft("blocked"),
  },
  pending: {
    text: "Связь прервалась, поэтому Ayla уточнит, успела ли запись создаться.",
    keepsDraft: outcomeKeepsDraft("pending"),
  },
  failed: {
    text: "Не получилось создать запись. Попробуй ещё раз.",
    keepsDraft: outcomeKeepsDraft("failed"),
  },
};

/** Девять состояний блока Recovery / Empty / Error макета. */
export type RecoveryKind =
  | "no_time"
  | "slot_unavailable"
  | "slot_taken"
  | "provider_unavailable"
  | "option_unavailable"
  | "network"
  | "unconfirmed"
  | "empty_list"
  | "offline";

export interface RecoveryAction {
  readonly label: string;
  /** Куда ведёт; `null` — действие экрана, а не переход. */
  readonly to: string | null;
}

export interface RecoveryContext {
  /** Имя специалиста, если оно известно кадру. */
  readonly provider?: string;
}

export interface RecoveryFrame {
  readonly text: (ctx: RecoveryContext) => string;
  readonly actions: readonly RecoveryAction[];
  /**
   * Действие кадра повторяет ТОТ ЖЕ запрос, а не создаёт новый.
   * Осмысленно только там, где речь о созданной (или не созданной)
   * записи, — отсюда и поле, а не общий флаг на всех.
   */
  readonly repeatsSameRequest?: boolean;
}

const OTHER_DATES: RecoveryAction = { label: "Другие даты", to: null };
const OTHER_PROVIDER: RecoveryAction = { label: "Другой специалист", to: "/customer/book/master" };
const SHOW_OTHER_PROVIDER: RecoveryAction = {
  label: "Показать другого специалиста",
  to: "/customer/book/master",
};
const SHOW_OTHER_OPTION: RecoveryAction = {
  label: "Показать другой вариант",
  to: "/customer/booking/option",
};
const PICK_OTHER_TIME: RecoveryAction = { label: "Выбрать другое время", to: null };
const RETRY: RecoveryAction = { label: "Повторить", to: null };
const CHECK_STATUS: RecoveryAction = { label: "Проверить статус", to: null };
/**
 * «Вернуться к Ayla» — форма макета. В коде кадра успеха живёт
 * `RETURN_TO_CHAT_LABEL = "Вернуться в чат"`, запертая DRF-1777. Обе
 * формы названы в теле PR; выбирать между двумя текстами владельца
 * самостоятельно мы не станем, поэтому здесь — слова этого макета, а
 * прежний кадр свои не менял.
 */
const BACK_TO_AYLA: RecoveryAction = { label: "Вернуться к Ayla", to: null };

export const RECOVERY: Record<RecoveryKind, RecoveryFrame> = {
  no_time: {
    text: ({ provider }) =>
      provider
        ? `У ${provider} пока нет подходящего времени.`
        : "Пока нет подходящего времени.",
    actions: [OTHER_DATES, OTHER_PROVIDER, BACK_TO_AYLA],
  },
  slot_unavailable: {
    text: () => "Это время только что стало недоступно.",
    actions: [PICK_OTHER_TIME],
  },
  slot_taken: {
    text: () => "Это время уже заняли.",
    actions: [PICK_OTHER_TIME],
  },
  provider_unavailable: {
    text: ({ provider }) =>
      provider
        ? `${provider} сейчас недоступна для этого варианта.`
        : "Специалист сейчас недоступен для этого варианта.",
    actions: [SHOW_OTHER_PROVIDER],
  },
  option_unavailable: {
    text: () => "Этот вариант сейчас недоступен.",
    actions: [SHOW_OTHER_OPTION, BACK_TO_AYLA],
  },
  network: {
    text: () => "Не удалось загрузить данные.",
    actions: [RETRY],
  },
  unconfirmed: {
    text: () => "Связь прервалась, поэтому Ayla уточнит, успела ли запись создаться.",
    actions: [CHECK_STATUS],
    repeatsSameRequest: true,
  },
  empty_list: {
    text: () => "Записей пока нет.",
    actions: [BACK_TO_AYLA],
  },
  offline: {
    text: () => "Нет соединения. Показаны последние загруженные данные.",
    // Макет: «Действия недоступны». Кнопка, которая ничего не может
    // сделать, хуже её отсутствия — она обещает связь, которой нет.
    actions: [],
  },
};
