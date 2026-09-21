/**
 * Карточка C04 «направление + почему» на экране (DRF-1769, К-3 N3).
 *
 * Экран — тупой рисовальщик ЗАПИСИ. Что показать, решил бот, когда
 * показывал карточку в чате: направление взято из курируемой таблицы
 * владельца, причины собраны из того, что человек сказал в этом пути, а
 * другие подходы — из той же таблицы. Здесь ничего из этого не
 * пересобирается: второй источник истины для фраз владельца однажды
 * разошёлся бы с первым, и экран показал бы не то, что сказал бот.
 *
 * Лист карты разрывов старше §60: условия «карточка до D10 не строится»
 * и «флаг до VERIFIED > 0» сняты решением владельца — направление стало
 * объектом пилота, и в DM карточка уже живёт без флага.
 *
 * Тексты — общий словарь с ботом (`apps/recommendation/card.py`);
 * равенство держит тест паритета, а не обещание в комментарии.
 */
import { request } from "./api";

/** Кадр C04.1 — дословно с макета DRF-1270. */
export const CARD_HEAD = "Моё лучшее направление для тебя:";
export const WHY_HEAD = "Почему это подходит тебе";
/** Кадр C04.3 — раскрытие причин по запросу. */
export const WHY_MORE_HEAD = "Почему это важно для тебя";

/** Кадр C04.2 — другие подходы. */
export const ALT_HEAD = "Есть несколько подходящих направлений. Что для тебя важнее?";
export const ALT_PRIMARY_HEAD = "Основной вариант (рекомендую):";
export const ALT_OTHERS_HEAD = "Другие подходы (тоже подойдут):";

export const BUTTON_PICK = "Подобрать вариант";
export const BUTTON_WHY = "Почему";
export const BUTTON_ALT = "Другой вариант";
export const BUTTON_SKIP = "Не сейчас";

/**
 * Сколько причин показываем. Больше трёх не присылает и бот — предел
 * держится с двух сторон: список причин приходит с сервера, но экран,
 * который молча нарисует четвёртую, однажды её и нарисует.
 */
export const MAX_REASONS = 3;

/**
 * Карточки нет — и экран говорит это словами владельца, теми же, что
 * звучат в чате (`recommendation-absence.ts`). Почему честный кадр, а не
 * пустой экран: пустота — второй способ соврать (класс #1918/#2198).
 */
export const GONE_TEXT =
  "Этой карточки у меня больше нет. Напиши, чего хочется, — и я подберу заново.";

export interface RecommendationAlternative {
  readonly what: string;
  readonly subline: string;
}

export interface RecommendationCard {
  readonly id: string;
  /** `direction` — карточка C04.1; `absence` — честное «нет рекомендации». */
  readonly kind: "direction" | "absence";
  readonly what: string;
  readonly subline: string;
  readonly why: readonly string[];
  readonly alternatives: readonly RecommendationAlternative[];
}

interface Envelope {
  data: RecommendationCard;
}

/**
 * GET /recommendation/<id> — то, что человеку показали.
 *
 * 404 — чужая, стёртая или несуществующая запись, одним ответом: id не
 * должен становиться оракулом «а есть ли у неё карточка». Экран рисует
 * на это честный кадр, а не пустоту.
 */
export const fetchRecommendation = async (id: string): Promise<RecommendationCard> => {
  const env = await request<Envelope>(`/recommendation/${id}`, { method: "GET" });
  return env.data;
};
