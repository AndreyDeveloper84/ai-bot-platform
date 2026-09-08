/**
 * Тесты `customer-booking.ts` — клиентской библиотеки воронки записи.
 *
 * После T7 (DRF-1568) полка подбора читает решение резолвера:
 * `{data: {resolver_spec_version, ordered[], …}}` — контракт
 * `docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md` §4.2/§9.4.
 * Прежняя форма `{recommendations: [{service_id, score}]}` и трёхслойная
 * форма источника здесь остались — но как фикстуры того, что обязано
 * краснеть. HTTP замокан; фикстуры зеркала — дословные формы
 * `apps/miniapp_api/views.py::_service_to_dict/_master_to_dict`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    fetchMaster: vi.fn(),
    fetchSlots: vi.fn(),
    createBooking: vi.fn(),
    fetchServices: vi.fn(),
    fetchMasters: vi.fn(),
    fetchRecommendations: vi.fn(),
  };
});

import {
  createBooking,
  fetchMasters,
  fetchRecommendations,
  fetchServices,
  fetchSlots,
  decisionContractViolation,
  ApiError,
  type Master,
  type Service,
} from "./api";
import {
  createCustomerBooking,
  getCatalogBrowse,
  getCustomerSlots,
} from "./customer-booking";

const mockedFetchSlots = vi.mocked(fetchSlots);
const mockedCreateBooking = vi.mocked(createBooking);
const mockedFetchServices = vi.mocked(fetchServices);
const mockedFetchMasters = vi.mocked(fetchMasters);
const mockedFetchRecommendations = vi.mocked(fetchRecommendations);

function service(partial: Partial<Service> & Pick<Service, "id" | "name">): Service {
  return {
    slug: partial.id,
    short_description: "",
    description: "",
    price_from: "1800.00",
    duration_min: 60,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
    ...partial,
  };
}

const MASTER: Master = {
  id: "mst-1",
  name: "Анна Соколова",
  specialization: "nail-мастер",
  bio: "",
  experience: "5 лет",
  rating: "4.9",
  photo_url: "",
};

/**
 * DRF-1556 — the loud channel is `console.error` (the only one
 * `apps/miniapp/src` has). Spied on in every test so «is it silent?» is
 * an assertion rather than an assumption, and so a real mismatch never
 * prints into the test log.
 */
function silenceConsoleError() {
  return vi.spyOn(console, "error").mockImplementation(() => {});
}
let consoleErrorSpy: ReturnType<typeof silenceConsoleError>;

beforeEach(() => {
  vi.clearAllMocks();
  consoleErrorSpy = silenceConsoleError();
});

afterEach(() => {
  consoleErrorSpy.mockRestore();
});

// --- booking flow (slots window / create passthrough) ----------------------

describe("getCustomerSlots", () => {
  it("requests a 14-day window by default with YYYY-MM-DD dates", async () => {
    mockedFetchSlots.mockResolvedValue({ slots: [] });
    await getCustomerSlots({ masterId: "mst-1", serviceId: "svc-1" });
    expect(mockedFetchSlots).toHaveBeenCalledTimes(1);
    const arg = mockedFetchSlots.mock.calls[0]![0];
    expect(arg.masterId).toBe("mst-1");
    expect(arg.serviceId).toBe("svc-1");
    expect(arg.dateFrom).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(arg.dateTo).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const from = new Date(`${arg.dateFrom}T00:00:00`);
    const to = new Date(`${arg.dateTo}T00:00:00`);
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000)).toBe(14);
  });

  it("honours an explicit days override", async () => {
    mockedFetchSlots.mockResolvedValue({ slots: [] });
    await getCustomerSlots({ masterId: "mst-1", serviceId: "svc-1", days: 7 });
    const arg = mockedFetchSlots.mock.calls[0]![0];
    const from = new Date(`${arg.dateFrom}T00:00:00`);
    const to = new Date(`${arg.dateTo}T00:00:00`);
    expect(Math.round((to.getTime() - from.getTime()) / 86_400_000)).toBe(7);
  });
});

describe("createCustomerBooking", () => {
  it("passes the payload through to the API layer verbatim", async () => {
    const created = {
      booking: {
        id: "b-1",
        service_name: "Маникюр",
        master_name: "Анна",
        visit_at: "2026-08-01T16:00:00+03:00",
        duration_min: 60,
        status: "confirmed",
      },
    };
    mockedCreateBooking.mockResolvedValue(created);
    const payload = {
      service_id: "svc-1",
      master_id: "mst-1",
      visit_at: "2026-08-01T16:00:00+03:00",
    };
    const result = await createCustomerBooking(payload);
    expect(mockedCreateBooking).toHaveBeenCalledWith(payload);
    expect(result).toBe(created);
  });

  it("propagates API errors to the caller (screen renders the error state)", async () => {
    mockedCreateBooking.mockRejectedValue(new Error("[409] unavailable: slot taken"));
    await expect(
      createCustomerBooking({
        service_id: "svc-1",
        master_id: "mst-1",
        visit_at: "2026-08-01T16:00:00+03:00",
      }),
    ).rejects.toThrow("[409]");
  });
});

// --- catalog browse: решение резолвера доезжает до экрана -----------------

/**
 * Конформное решение §4.2. Собрано из ДОКУМЕНТА
 * (`docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md`), а не из
 * чужой реализации: фикстура, списанная с кода источника, доказывает
 * лишь то, что мы повторили источник, включая его ошибки.
 */
function decision(
  ordered: unknown[],
  extra: Record<string, unknown> = {},
): unknown {
  return {
    data: {
      decision_id: "dec-1",
      request_id: "req-1",
      resolver_spec_version: "1.0",
      policy_versions: { resolver_spec_version: "1.0" },
      ordered,
      ...extra,
    },
  };
}

function candidate(
  id: string,
  partial: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    candidate: { kind: "SERVICE", id },
    rank: 1,
    tier: 1,
    reason_codes: ["MATCH_SERVICE_EXACT"],
    ...partial,
  };
}

describe("getCatalogBrowse — решение резолвера", () => {
  function mirrorReady(): void {
    mockedFetchServices.mockResolvedValue({
      services: [
        service({ id: "svc-1", name: "Маникюр" }),
        service({ id: "svc-2", name: "Педикюр" }),
        service({ id: "svc-3", name: "Массаж" }),
      ],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  }

  it("отдаёт порядок источника нетронутым и не пересобирает его", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        candidate("svc-3", { rank: 1, tier: 1, reason_codes: ["MATCH_GOAL_CATEGORY"] }),
        candidate("svc-1", { rank: 2, tier: 2, reason_codes: ["MATCH_SERVICE_EXACT"] }),
        candidate("svc-2", { rank: 3, tier: 2, reason_codes: ["EXEC_BOOKABLE"] }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks.map((p) => p.serviceId)).toEqual(["svc-3", "svc-1", "svc-2"]);
    expect(data.picks.map((p) => p.rank)).toEqual([1, 2, 3]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("собирает WHY из утверждённых кодов, а не из присланной фразы", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        candidate("svc-1", {
          reason_codes: ["EXEC_BOOKABLE", "MATCH_SERVICE_EXACT", "SCOPE_WITHIN_CITY"],
        }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([
      {
        serviceId: "svc-1",
        tier: 1,
        rank: 1,
        reasonCodes: ["EXEC_BOOKABLE", "MATCH_SERVICE_EXACT", "SCOPE_WITHIN_CITY"],
        reasons: ["Можно записаться", "Это та услуга, которую ты искала", "В твоём городе"],
      },
    ]);
  });

  it("режет WHY до трёх строк — решение владельца 25.08", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        candidate("svc-1", {
          reason_codes: [
            "CONTEXT_PRIOR_COMPLETED_VISIT",
            "ELIG_ACTIVE_OFFER",
            "EXEC_BOOKABLE",
            "MATCH_SERVICE_EXACT",
            "SCOPE_WITHIN_CITY",
          ],
        }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks[0]!.reasons).toHaveLength(3);
    // Коды при этом доезжают ВСЕ: режется показ, а не свидетельство.
    expect(data.picks[0]!.reasonCodes).toHaveLength(5);
  });

  it("равный ярус остаётся равным: ни один pick не помечен лучшим", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        candidate("svc-1", { rank: 1, tier: 1 }),
        candidate("svc-2", { rank: 2, tier: 1 }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks.map((p) => p.tier)).toEqual([1, 1]);
    // Присутствие: ярус доехал… (§4.3 / решение владельца §29.3)
    expect(data.picks[0]!.tier).toBe(data.picks[1]!.tier);
    // …отсутствие: и ни одного поля, которым можно назвать первого лучшим.
    for (const pick of data.picks) {
      expect(Object.keys(pick).sort()).toEqual([
        "rank",
        "reasonCodes",
        "reasons",
        "serviceId",
        "tier",
      ]);
    }
  });

  it("кандидат, которому нечем объяснить, не проходит гейт WHY", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        // Оба кода в реестре есть, но человеку они не причина.
        candidate("svc-1", { reason_codes: ["TIE_TIER_SHARED", "MATCH_UNDETERMINED"] }),
        candidate("svc-2", { rank: 2, reason_codes: ["MATCH_SERVICE_EXACT"] }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks.map((p) => p.serviceId)).toEqual(["svc-2"]);
    // Это гейт владельца, а не расхождение: шуметь тут нечем.
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("код вне реестра фразы не даёт и нарушением не является", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        candidate("svc-1", { reason_codes: ["MATCH_SERVICE_EXACT", "REASON_FROM_THE_FUTURE"] }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks[0]!.reasons).toEqual(["Это та услуга, которую ты искала"]);
    expect(data.picks[0]!.reasonCodes).toContain("REASON_FROM_THE_FUTURE");
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("кандидат не-услуга на полку услуг не попадает", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([
        { ...candidate("mst-1"), candidate: { kind: "PROVIDER", id: "mst-1" } },
        candidate("svc-1", { rank: 2 }),
      ]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks.map((p) => p.serviceId)).toEqual(["svc-1"]);
  });

  it("услуга, которой нет в зеркале, до экрана не доходит", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([candidate("svc-ghost"), candidate("svc-1", { rank: 2 })]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks.map((p) => p.serviceId)).toEqual(["svc-1"]);
  });

  it("пустой ordered — законный ответ, а не расхождение", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(decision([]));
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("отказ зеркала отвергается — экран рисует ошибку", async () => {
    mockedFetchServices.mockRejectedValue(new Error("[500] http_error"));
    mockedFetchMasters.mockResolvedValue({ masters: [] });
    await expect(getCatalogBrowse()).rejects.toThrow("[500]");
  });
});

// --- ПРИЁМКА T7, шаг 1: ДО — сегодняшний ответ краснеет --------------------
//
// Три шага целиком: «до» краснеет, «после» зеленеет, и проверка
// ОСТАЁТСЯ строгой. Без третьего шага зелёное доказывало бы только то,
// что проверку ослабили.

describe("приёмка T7 · шаг 1 — трёхслойный ответ источника даёт CONTRACT_VIOLATION", () => {
  /** Форма, которую источник отдаёт на 07.09.2026: слои с МАСТЕРАМИ. */
  const LAYERED_PAYLOAD = {
    data: {
      layer_1_your_places: [],
      layer_2_ayla_picks: [{ master_id: "mst-1", reasoning_text: "20 минут от тебя" }],
      layer_3_explore: [],
    },
  };

  /** Форма, которую этот файл объявлял ДО T7. */
  const LEGACY_PAYLOAD = {
    recommendations: [{ service_id: "svc-1", score: 0.9, reasons: ["20 минут от тебя"] }],
  };

  beforeEach(() => {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  });

  it("говорит об этом вслух и не подделывает подбор", async () => {
    mockedFetchRecommendations.mockResolvedValue(LAYERED_PAYLOAD);
    const data = await getCatalogBrowse();

    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const message = String(consoleErrorSpy.mock.calls[0]![0]);
    expect(message).toContain("расхождение контракта");
    expect(message).toContain("resolver_spec_version");

    expect(data.picks).toEqual([]);
    expect(data.services).toHaveLength(1);
    expect(data.masters).toHaveLength(1);
  });

  it("прежняя форма {service_id, score} тоже краснеет — шаг 3", async () => {
    mockedFetchRecommendations.mockResolvedValue(LEGACY_PAYLOAD);
    const data = await getCatalogBrowse();
    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    expect(String(consoleErrorSpy.mock.calls[0]![0])).toContain("конверт");
    expect(data.picks).toEqual([]);
  });

  it("не уносит значения в журнал — только ключи и типы", async () => {
    mockedFetchRecommendations.mockResolvedValue({
      data: {
        decision_id: "dec-1",
        request_id: "req-1",
        resolver_spec_version: "1.0",
        policy_versions: {},
        ordered: [
          {
            candidate: { kind: "SERVICE", id: "svc-1" },
            rank: 1,
            tier: "первый",
            reason_codes: ["MATCH_SERVICE_EXACT"],
            secret: "+79990000000",
          },
        ],
      },
    });
    await getCatalogBrowse();

    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const message = String(consoleErrorSpy.mock.calls[0]![0]);
    // Присутствие: поле названо…
    expect(message).toContain("ordered[0].tier");
    // …отсутствие: и ни одного значения за ключами.
    expect(message).not.toContain("+79990000000");
    expect(message).not.toContain("первый");
    expect(message).not.toContain("svc-1");
  });
});

// --- ПРИЁМКА T7, шаг 2 и молчание недоступности ---------------------------

describe("недоступный источник МОЛЧИТ (парность DRF-1411)", () => {
  beforeEach(() => {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  });

  const outages: ReadonlyArray<[string, unknown]> = [
    ["сеть упала", new TypeError("Failed to fetch")],
    ["502 от прокси", new ApiError(502, "ayla_unavailable", "upstream down")],
    ["500 от прокси", new ApiError(500, "http_error", "Internal Server Error")],
    ["таймаут", Object.assign(new Error("The operation was aborted."), { name: "AbortError" })],
    ["тело не разбирается", new SyntaxError("Unexpected token < in JSON at position 0")],
  ];

  for (const [label, failure] of outages) {
    it(`молчит и пусто, когда источник недоступен: ${label}`, async () => {
      mockedFetchRecommendations.mockRejectedValue(failure);
      const data = await getCatalogBrowse();
      // Присутствие — каталог экран всё равно получает…
      expect(data.services).toHaveLength(1);
      expect(data.masters).toHaveLength(1);
      // …отсутствие — подбор не выдуман и НИ ОДНОГО слова в журнал.
      expect(data.picks).toEqual([]);
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    });
  }

  it("приёмка T7 · шаг 2 — конформное решение даёт OK и молчит", async () => {
    mockedFetchRecommendations.mockResolvedValue(
      decision([candidate("svc-1", { reason_codes: ["MATCH_SERVICE_EXACT"] })]),
    );
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([
      {
        serviceId: "svc-1",
        tier: 1,
        rank: 1,
        reasonCodes: ["MATCH_SERVICE_EXACT"],
        reasons: ["Это та услуга, которую ты искала"],
      },
    ]);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });
});

// --- ПРИЁМКА T7, шаг 3: проверка формы осталась строгой --------------------

describe("decisionContractViolation", () => {
  it("пропускает всё, что контракт разрешает", () => {
    const conforming: unknown[] = [
      decision([]),
      decision([candidate("svc-1")]),
      decision([candidate("svc-1", { tier: 0, rank: 0 })]),
      decision([candidate("svc-1", { evidence: [] })]),
      decision([
        candidate("svc-1", {
          evidence: [
            {
              kind: "RATING",
              value: 4.9,
              strength: "UNSUBSTANTIATED",
              origin: "DOMAIN_FACT",
            },
          ],
        }),
      ]),
      decision([{ ...candidate("mst-1"), candidate: { kind: "PROVIDER", id: "mst-1" } }]),
      // Вперёд-совместимость: незнакомые ЛИШНИЕ поля — не расхождение.
      decision([candidate("svc-1", { stage_verdicts: { S2: "DISTINGUISHED" } })], {
        excluded: [],
        computed_at: "2026-09-08T10:00:00+03:00",
        something_new: 1,
      }),
      // Минорная версия старше нашей — разбираем: мажорная та же.
      { ...(decision([]) as { data: Record<string, unknown> }) },
    ];
    for (const payload of conforming) {
      expect(decisionContractViolation(payload)).toBeNull();
    }
    const minorAhead = decision([]) as { data: Record<string, unknown> };
    minorAhead.data.resolver_spec_version = "1.7";
    expect(decisionContractViolation(minorAhead)).toBeNull();
  });

  it("называет первое нарушение на каждый способ сломать контракт", () => {
    const broken = (
      ordered: unknown[],
      extra: Record<string, unknown> = {},
    ): unknown => decision(ordered, extra);
    const stripped = (field: string): unknown => {
      const payload = decision([]) as { data: Record<string, unknown> };
      delete payload.data[field];
      return payload;
    };
    const cases: ReadonlyArray<[unknown, string]> = [
      [null, "ожидался объект"],
      [[], "ожидался объект"],
      ["", "ожидался объект"],
      [{}, "ожидался конверт"],
      // Полдефекта C-02: ответ без конверта.
      [{ decision_id: "d", request_id: "r", ordered: [] }, "ожидался конверт"],
      [{ data: {} }, "resolver_spec_version"],
      [{ data: { resolver_spec_version: 1 } }, "resolver_spec_version"],
      // Неизвестная мажорная версия — не разбираем вовсе (§9.4).
      [{ data: { resolver_spec_version: "2.0" } }, "неизвестная мажорная версия"],
      [{ data: { resolver_spec_version: "х.0" } }, "неизвестная мажорная версия"],
      [{ data: { resolver_spec_version: "1.0" } }, "ordered отсутствует"],
      [broken([null]), "ordered[0]: ожидался объект"],
      [broken([{ rank: 1, tier: 1, reason_codes: ["X"] }]), "ordered[0].candidate"],
      [
        broken([{ ...candidate("s"), candidate: { kind: "SERVICE", id: 7 } }]),
        "ordered[0].candidate",
      ],
      // `kind` вне четырёх — «услуга» и «мастер» перестали различаться.
      [
        broken([{ ...candidate("s"), candidate: { kind: "МАСТЕР", id: "s" } }]),
        "ordered[0].candidate.kind",
      ],
      [broken([{ ...candidate("s"), candidate: { id: "s" } }]), "ordered[0].candidate.kind"],
      [broken([candidate("s", { rank: "1" })]), "ordered[0].rank"],
      [broken([candidate("s", { tier: 1.5 })]), "ordered[0].tier"],
      [broken([candidate("s", { tier: undefined })]), "ordered[0].tier"],
      [broken([candidate("s", { reason_codes: [] })]), "ordered[0].reason_codes"],
      [broken([candidate("s", { reason_codes: "MATCH_SERVICE_EXACT" })]), "reason_codes"],
      [broken([candidate("s", { reason_codes: [1] })]), "reason_codes"],
      [broken([candidate("s", { evidence: {} })]), "ordered[0].evidence"],
      [stripped("decision_id"), "decision_id"],
      [stripped("request_id"), "request_id"],
      [stripped("policy_versions"), "policy_versions"],
      // §8.4 E1 — строка для показа человеку в ответе запрещена…
      [broken([candidate("s", { reasoning_text: "Рейтинг 4.9" })]), "строку для показа"],
      // …в том числе вложенная.
      [
        broken([candidate("s", { evidence: [{ kind: "RATING", why_text: "Рейтинг 4.9" }] })]),
        "строку для показа",
      ],
      [broken([], { reason_text: "потому что" }), "строку для показа"],
      // Второй элемент сломан — проверка не останавливается на первом…
      [broken([candidate("s"), candidate("s2", { rank: 2, tier: "два" })]), "ordered[1].tier"],
    ];
    for (const [payload, expected] of cases) {
      const violation = decisionContractViolation(payload);
      expect(violation, `payload: ${JSON.stringify(payload)}`).not.toBeNull();
      expect(violation).toContain(expected);
    }
  });

  it("частично конформный ответ невалиден ЦЕЛИКОМ (§9.4.1, OD §53.1)", async () => {
    // Девятнадцать годных и один битый. «Пропускать годные» запрещено:
    // это вернуло бы четвёртого авторитета, живущего в фильтре.
    const ordered: unknown[] = [];
    for (let i = 1; i <= 19; i += 1) {
      ordered.push(candidate(`svc-${i}`, { rank: i, tier: 1 }));
    }
    ordered.push(candidate("svc-20", { rank: 20, tier: 1, reason_codes: [] }));

    const violation = decisionContractViolation(decision(ordered));
    expect(violation).toContain("ответ невалиден целиком");
    expect(violation).toContain("ordered[19].reason_codes");

    // И то же самое на живом пути: ни одна из девятнадцати не доезжает.
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
    mockedFetchRecommendations.mockResolvedValue(decision(ordered));
    const data = await getCatalogBrowse();
    expect(data.picks).toEqual([]);
    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
  });
});

// --- §76: пустота с именем, а не безымянная пустота ------------------------
//
// Решение владельца 08.09.2026: `VERIFIED` выдаётся только после
// подтверждения, 206 существующих связей становятся `REVIEW_REQUIRED`,
// и ноль `VERIFIED` НЕ разрешает fallback. Когда пригодных к
// рекомендации кандидатов нет, источник отвечает ШТАТНЫМ результатом,
// а не ошибкой, и человеку показывается неперсонализированное
// состояние — каталог и запись по прямому выбору, без слова «подходит».
//
// Форма этого состояния на проводе описана контрактом §10.3: пустой
// `ordered[]` ПЛЮС код `ELIG_EXCLUDED_NOT_RECOMMENDABLE`. Соседние
// пустоты — блоком ниже: их у полки три, и снаружи они одинаковы.

describe("§76 · NO_VERIFIED_CANDIDATES — штатное состояние с именем", () => {
  function mirrorReady(): void {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  }

  it("узнаётся по коду решения и НЕ выдаётся за ошибку", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }),
    );
    const data = await getCatalogBrowse();

    expect(data.picksOutcome).toBe("NO_VERIFIED_CANDIDATES");
    expect(data.picks).toEqual([]);
    // Каталог и мастера на месте — это и есть предусмотренное
    // неперсонализированное состояние, а не пустой экран.
    expect(data.services).toHaveLength(1);
    expect(data.masters).toHaveLength(1);
    // Штатное состояние молчит: шум здесь обесценил бы детектор
    // расхождения, стоящий в соседней ветке.
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("пустой ordered БЕЗ кода — это другое состояние, и оно не подменяется", async () => {
    mirrorReady();
    mockedFetchRecommendations.mockResolvedValue(decision([]));
    const data = await getCatalogBrowse();
    // «Никто не подошёл» ≠ «нечего рекомендовать, потому что не
    // проверено». Контракт различает их кодом — различаем и мы.
    expect(data.picksOutcome).toBe("OK");
    expect(data.picks).toEqual([]);
  });

  it("REVIEW_REQUIRED, доехавший до клиента, — нарушение, а не повод отрисовать", () => {
    // «Клиенту нельзя сообщать, что такая услуга или мастер подходит».
    for (const status of ["REVIEW_REQUIRED", "UNMAPPED", "UNKNOWN", null]) {
      const violation = decisionContractViolation(
        decision([candidate("svc-1", { mapping_status: status })]),
      );
      expect(violation, `mapping_status=${String(status)}`).toContain("mapping_status");
    }
    // А `VERIFIED` проходит — иначе сторож запрещал бы всё подряд.
    expect(
      decisionContractViolation(decision([candidate("svc-1", { mapping_status: "VERIFIED" })])),
    ).toBeNull();
  });

  it("код исключения внутри ordered[] — нарушение (§4.4, §10.2)", () => {
    const cases = [
      "ELIG_EXCLUDED_NOT_RECOMMENDABLE",
      "ELIG_EXCLUDED_SAFETY",
      "SCOPE_EXCLUDED_OUT_OF_CITY",
      "SCOPE_GEO_UNKNOWN_EXCLUDED",
    ];
    for (const code of cases) {
      const violation = decisionContractViolation(
        decision([candidate("svc-1", { reason_codes: ["MATCH_SERVICE_EXACT", code] })]),
      );
      expect(violation, code).toContain("код исключения");
    }
    // Тот же код НА УРОВНЕ РЕШЕНИЯ законен — это и есть §10.3.
    expect(
      decisionContractViolation(
        decision([], { reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }),
      ),
    ).toBeNull();
  });
});

// --- Три пустоты, неразличимые снаружи -------------------------------------
//
// `200` и пустой `ordered[]` у всех трёх. Различает их только код, и
// цена путаницы разная у каждой пары: «нет подтверждённых связей»
// говорит «почини разметку», отказ по безопасности — «не чини ничего,
// гейт сработал», «никто не совпал» — «дело в запросе, не в системе».
//
// Читаются ДВА места: реализация резолвера
// (`recommendation/_pipeline.py::_decision_codes`) поднимает на уровень
// решения только первые два кода, третий живёт лишь в `excluded[]`.

describe("три пустоты различаются кодом, а не пустотой ordered", () => {
  function mirrorReady(): void {
    mockedFetchServices.mockResolvedValue({
      services: [service({ id: "svc-1", name: "Маникюр" })],
    });
    mockedFetchMasters.mockResolvedValue({ masters: [MASTER] });
  }

  function excluded(id: string, code: string): unknown {
    return { candidate: { kind: "SERVICE", id }, stage: "S1", reason_code: code };
  }

  beforeEach(mirrorReady);

  it("код решения: безопасность и неподтверждённая связь названы по-разному", async () => {
    mockedFetchRecommendations.mockResolvedValue(
      decision([], {
        reason_codes: ["ELIG_EXCLUDED_SAFETY"],
        excluded: [excluded("svc-1", "ELIG_EXCLUDED_SAFETY")],
      }),
    );
    expect((await getCatalogBrowse()).picksOutcome).toBe("SAFETY_BLOCKED");

    mockedFetchRecommendations.mockResolvedValue(
      decision([], {
        reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"],
        excluded: [excluded("svc-1", "ELIG_EXCLUDED_NOT_RECOMMENDABLE")],
      }),
    );
    expect((await getCatalogBrowse()).picksOutcome).toBe("NO_VERIFIED_CANDIDATES");
  });

  it("NOT_CAPABLE читается из excluded[] — на уровень решения он не поднимается", async () => {
    // Резолвер этот код наверх не выносит. Потребитель, читающий одни
    // лишь коды решения, назвал бы это состояние `OK` с пустой полкой —
    // то есть снова безымянной пустотой.
    mockedFetchRecommendations.mockResolvedValue(
      decision([], { excluded: [excluded("svc-1", "ELIG_EXCLUDED_NOT_CAPABLE")] }),
    );
    expect((await getCatalogBrowse()).picksOutcome).toBe("NO_CAPABLE_CANDIDATES");
  });

  it("безопасность старше неподтверждённой связи, когда оба кода сразу", async () => {
    // Часть кандидатов отсеяна гейтом, часть — разметкой. Потерять
    // безопасность за более громким соседом опаснее, чем наоборот:
    // это единственный из трёх случаев, где чинить нечего.
    mockedFetchRecommendations.mockResolvedValue(
      decision([], {
        reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE", "ELIG_EXCLUDED_SAFETY"],
        excluded: [
          excluded("svc-1", "ELIG_EXCLUDED_SAFETY"),
          excluded("svc-2", "ELIG_EXCLUDED_NOT_RECOMMENDABLE"),
        ],
      }),
    );
    expect((await getCatalogBrowse()).picksOutcome).toBe("SAFETY_BLOCKED");
  });

  it("исключения при НЕпустой полке ничего не переименовывают", async () => {
    // Исключения — штатная часть любого решения. Пока `ordered` не пуст,
    // причина пустоты не обсуждается: пустоты нет.
    mockedFetchRecommendations.mockResolvedValue(
      decision([candidate("svc-1", { reason_codes: ["MATCH_SERVICE_EXACT"] })], {
        excluded: [excluded("svc-2", "ELIG_EXCLUDED_SAFETY")],
      }),
    );
    const data = await getCatalogBrowse();
    expect(data.picksOutcome).toBe("OK");
    expect(data.picks).toHaveLength(1);
  });

  it("ШЕСТЬ исходов различимы попарно — ни один не схлопнут в другой", async () => {
    const seen: string[] = [];

    mockedFetchRecommendations.mockResolvedValue(
      decision([candidate("svc-1", { reason_codes: ["MATCH_SERVICE_EXACT"] })]),
    );
    seen.push((await getCatalogBrowse()).picksOutcome);

    mockedFetchRecommendations.mockRejectedValue(new Error("[502] ayla_unavailable"));
    seen.push((await getCatalogBrowse()).picksOutcome);

    mockedFetchRecommendations.mockResolvedValue({ recommendations: [] });
    seen.push((await getCatalogBrowse()).picksOutcome);

    mockedFetchRecommendations.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_NOT_RECOMMENDABLE"] }),
    );
    seen.push((await getCatalogBrowse()).picksOutcome);

    mockedFetchRecommendations.mockResolvedValue(
      decision([], { reason_codes: ["ELIG_EXCLUDED_SAFETY"] }),
    );
    seen.push((await getCatalogBrowse()).picksOutcome);

    mockedFetchRecommendations.mockResolvedValue(
      decision([], { excluded: [excluded("svc-1", "ELIG_EXCLUDED_NOT_CAPABLE")] }),
    );
    seen.push((await getCatalogBrowse()).picksOutcome);

    expect(seen).toEqual([
      "OK",
      "UNAVAILABLE",
      "CONTRACT_VIOLATION",
      "NO_VERIFIED_CANDIDATES",
      "SAFETY_BLOCKED",
      "NO_CAPABLE_CANDIDATES",
    ]);
    expect(new Set(seen).size).toBe(6);
  });

  it("excluded[] проверяется, раз уж он читается", () => {
    const cases: ReadonlyArray<[unknown, string]> = [
      [decision([], { excluded: {} }), "excluded: ожидался список"],
      [decision([], { excluded: [null] }), "excluded[0]: ожидался объект"],
      [
        decision([], { excluded: [{ stage: "S1", reason_code: "ELIG_EXCLUDED_SAFETY" }] }),
        "excluded[0].candidate",
      ],
      [
        decision([], { excluded: [{ candidate: { kind: "SERVICE", id: "s" }, stage: "S1" }] }),
        "excluded[0].reason_code",
      ],
      // Код НЕ из семейства исключения: упорядочивание тайком стало бы
      // фильтром, а §4.4 существует ровно затем, чтобы этого не было.
      [
        decision([], {
          excluded: [
            { candidate: { kind: "SERVICE", id: "s" }, stage: "S1", reason_code: "MATCH_SERVICE_EXACT" },
          ],
        }),
        "ожидался код исключения",
      ],
      [
        decision([], {
          excluded: [{ candidate: { kind: "SERVICE", id: "s" }, reason_code: "ELIG_EXCLUDED_SAFETY" }],
        }),
        "excluded[0].stage",
      ],
    ];
    for (const [payload, expected] of cases) {
      const violation = decisionContractViolation(payload);
      expect(violation, JSON.stringify(payload)).not.toBeNull();
      expect(violation).toContain(expected);
    }
    // А правильная запись проходит.
    expect(
      decisionContractViolation(
        decision([], { excluded: [excluded("svc-1", "ELIG_EXCLUDED_SAFETY")] }),
      ),
    ).toBeNull();
  });
});
