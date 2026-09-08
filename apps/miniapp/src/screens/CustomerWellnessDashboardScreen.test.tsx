/**
 * Layout + honesty tests for `CustomerWellnessDashboardScreen`.
 *
 * The reads are wired to the backend, so the dashboard no longer
 * invents anyone's day. The screen-level gate came off with DRF-1546
 * (решение владельца §24.2 + §34) — a prod build now renders the real
 * home screen, and the tests below hold that open in both directions:
 * the screen must render, AND nothing without a live handle may come
 * back with it.
 *
 * The reads are mocked here rather than left to hit the network — this
 * file is about the layout, and a screen test that also exercised HTTP
 * would fail for reasons that have nothing to do with it.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return {
    ...original,
    getCatalogBrowse: vi.fn(),
  };
});

// DRF-1493: экран объявляет свой вид через `useScreenBack`, а тот
// заводит аппаратную кнопку MAX — мок должен отдавать и её ручки,
// иначе тест падает на отсутствующем экспорте, а не на поведении.
vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
  setBackButton: () => undefined,
  onBackButton: () => () => undefined,
}));

import { getCatalogBrowse } from "../lib/customer-booking";

const mockedBrowse = vi.mocked(getCatalogBrowse);


/**
 * The reads are wired to the backend now, so a dev build without
 * `?stub=` goes to the network. These tests are about the GATE and the
 * LAYOUT, not about the data source — so they ask for the stub
 * explicitly, which is exactly what `?stub=` exists for.
 *
 * That the wired reads call the right endpoints is proven separately,
 * in `customer-wellness.test.ts`.
 */
function useDevStubData() {
  window.history.replaceState({}, "", "/customer/main?stub=default");
}

/**
 * Payload for the prod-build cases. A production bundle ignores `?stub=`
 * by design (`pickStubOrLive` returns `null` unconditionally there), so
 * those cases are served over `fetch`, exactly like the DRF-1476 ones
 * further down.
 */
function serveLiveHome(today: Record<string, unknown> = {}) {
  const body = {
    calories_eaten: 1240,
    calories_target: 2100,
    pfc: { protein_g: 65, fat_g: 40, carbs_g: 120 },
    water_glasses_eaten: 4,
    water_glasses_target: 8,
    active_goals: [],
    display_name: "Анна",
    ...today,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const u = String(url);
      const payload = u.includes("/wellness/today")
        ? body
        : u.includes("/recent-activity")
          ? { this_week_booking_count: 0 }
          : null;
      if (payload === null) throw new Error(`unexpected fetch: ${u}`);
      return {
        ok: true,
        status: 200,
        json: async () => payload,
      } as unknown as Response;
    }),
  );
}

/** Fetch must never be reached in the DEV cases — if it is, the stub
 *  selection above silently stopped working and the test would go
 *  green against real network shape instead of the layout. */
function forbidNetwork() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("network reached: ?stub= selection is broken");
    }),
  );
}

async function renderScreen(prod: boolean) {
  vi.resetModules();
  if (prod) vi.stubEnv("DEV", false);
  try {
    const { CustomerWellnessDashboardScreen } = await import(
      "./CustomerWellnessDashboardScreen"
    );
    render(
      <MemoryRouter initialEntries={["/customer/main"]}>
        <CustomerWellnessDashboardScreen />
      </MemoryRouter>,
    );
  } finally {
    if (prod) vi.unstubAllEnvs();
  }
}

describe("CustomerWellnessDashboardScreen — the home surface", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({
      services: [],
      masters: [],
      picks: [],
    });
    useDevStubData();
    forbidNetwork();
  });

  it("DEV build: renders the dashboard", async () => {
    await renderScreen(false);
    expect(await screen.findByText(/Вода:/)).toBeInTheDocument();
    expect(screen.queryByText(/выдуманных данных/)).not.toBeInTheDocument();
  });

  it("prod build: renders the SAME dashboard — the gate is off (DRF-1546)", async () => {
    // До DRF-1546 здесь рисовался `PilotComingSoonScreen`, и человек на
    // «Главной» видел «скоро будет» вместо своего дня.
    serveLiveHome();
    await renderScreen(true);
    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.getByText(/1240 \/ 2100 ккал/)).toBeInTheDocument();
    // NEGATIVE (парная): заглушки больше нет.
    expect(screen.queryByText(/выдуманных данных/)).not.toBeInTheDocument();
  });

  it("prod build: the 📸 quick action is GONE, the ones with handles stay", async () => {
    // Правило владельца §33 / DRF-1543: за «📸 Сфотографируй еду» нет
    // ручки (`/api/v1/customer/food/*` = 404, `guardProd` бросает вне
    // DEV), поэтому её на главной нет. Парная положительная стража —
    // остальные быстрые действия обязаны работать, иначе «починить»
    // можно было бы, опустошив блок.
    serveLiveHome();
    await renderScreen(true);
    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();

    // Смотрим ровно в блок быстрых действий: «Найди услугу» есть ещё и
    // в пустом состоянии карточки записи, и без области проверка ловила
    // бы не ту кнопку.
    const qa = within(screen.getByRole("region", { name: "Что сделаем сейчас" }));
    // NEGATIVE: кнопки, ведущей на падающий экран, нет.
    expect(
      qa.queryByRole("button", { name: "Сфотографируй еду" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Сфотографируй еду/)).not.toBeInTheDocument();
    // POSITIVE (парная): три действия с живыми ручками на месте.
    expect(
      qa.getByRole("button", { name: "Добавить стакан воды 250 мл" }),
    ).toBeInTheDocument();
    expect(qa.getByRole("button", { name: "Выбери цель" })).toBeInTheDocument();
    expect(qa.getByRole("button", { name: "Найди услугу" })).toBeInTheDocument();
  });

  it("prod build: «Главная» is the active tab and «День» is not offered", async () => {
    // Поверхности «День» не существует — её роль исполнял этот экран.
    serveLiveHome();
    await renderScreen(true);
    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(
      within(nav).getByRole("button", { name: "Главная", current: "page" }),
    ).toBeInTheDocument();
    // NEGATIVE (парная): мёртвой вкладки нет...
    expect(
      within(nav).queryByRole("button", { name: "День" }),
    ).not.toBeInTheDocument();
    // ...а живые вкладки на месте.
    for (const tab of ["Записи", "Услуги", "Я"]) {
      expect(within(nav).getByRole("button", { name: tab })).toBeInTheDocument();
    }
  });

  const PEDIKYUR = {
    id: "svc-2",
    slug: "pedikyur",
    name: "Педикюр",
    short_description: "",
    description: "",
    price_from: "2200.00",
    duration_min: 90,
    is_popular: false,
    contraindications: "",
    is_bookable: true,
  };

  it("DEV build, Block 7: renders scorer picks WITH the WHY the source sent", async () => {
    mockedBrowse.mockResolvedValue({
      services: [PEDIKYUR],
      masters: [],
      picks: [
        {
          serviceId: "svc-2",
          tier: 1,
          rank: 1,
          reasonCodes: ["EXEC_SLOT_CONFIRMED_IN_WINDOW"],
          reasons: ["Есть свободное время в нужном окне"],
        },
      ],
    });
    await renderScreen(false);
    expect(
      await screen.findByRole("heading", { name: /Ayla подобрала тебе/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Педикюр")).toBeInTheDocument();
    expect(screen.getByText(/2 200 ₽/)).toBeInTheDocument();
    expect(screen.getByText("Есть свободное время в нужном окне")).toBeInTheDocument();
  });

  // Owner ruling 25.08 — same gate on the second branded surface.
  it("DEV build, Block 7: no WHY → no branded block, dashboard unaffected", async () => {
    mockedBrowse.mockResolvedValue({
      services: [PEDIKYUR],
      masters: [],
      picks: [],
    });
    await renderScreen(false);
    // Dashboard itself still renders.
    expect(await screen.findByText(/Вода:/)).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /Ayla подобрала тебе/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Педикюр")).not.toBeInTheDocument();
  });
});


/**
 * DRF-1476 — the dashboard must not contradict the goal screen.
 *
 * Owner walkthrough 2026-09-05: a goal was chosen and active, and this
 * dashboard offered «Выбери цель». `active_goals` was hardcoded `[]`.
 *
 * These tests drive the REAL read path (no `?stub=`), stubbing `fetch`,
 * so they cover the lib→screen wiring and not just the renderer. Every
 * «the CTA is gone» assertion is paired with a «the CTA is there» case
 * on the same code path — a fix that hid the CTA from everyone would
 * pass the first and fail the second.
 */
describe("CustomerWellnessDashboardScreen — goal truthfulness (DRF-1476)", () => {
  const BASE_TODAY = {
    calories_eaten: 777,
    calories_target: 1900,
    water_glasses_eaten: 3,
    water_glasses_target: 8,
    display_name: "Анна",
  };

  /** Route by URL so all three reads resolve; unknown URLs fail loudly. */
  function serve(today: unknown, activity: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? today
          : u.includes("/recent-activity")
            ? activity
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    // No `?stub=` — go through the wired read.
    window.history.replaceState({}, "", "/customer/main");
  });

  it("goal chosen: it is named on the dashboard and «Выбери цель» is gone", async () => {
    serve(
      {
        ...BASE_TODAY,
        active_goals: [{ title: "Позаботиться о коже лица", week_num: 2 }],
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: the person's actual goal is on screen, by name.
    expect(
      await screen.findByText(/Позаботиться о коже лица/),
    ).toBeInTheDocument();
    expect(screen.getByText("Моя цель")).toBeInTheDocument();
    // NEGATIVE (paired, same render): the bug is gone.
    expect(screen.queryByText("Выбери цель")).not.toBeInTheDocument();
  });

  it("no goal chosen: «Выбери цель» still shows, exactly as before", async () => {
    // The guard on the fix. Without it, «the CTA disappeared» would be
    // indistinguishable from a change that hides it from everyone.
    serve({ ...BASE_TODAY, active_goals: [] }, { this_week_booking_count: 0 });
    await renderScreen(false);

    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    expect(screen.getByText(/Цель не выбрана/)).toBeInTheDocument();
    expect(screen.queryByText("Моя цель")).not.toBeInTheDocument();
  });

  it("goal layer unreachable: neither claim is made", async () => {
    // `active_goals` absent — the backend could not ask. Telling this
    // person to choose a goal is the original defect, restored by an
    // outage; telling her she has one would be the mirror lie.
    serve({ ...BASE_TODAY }, { this_week_booking_count: 0 });
    await renderScreen(false);

    // POSITIVE: the dashboard rendered and the goal row is honest.
    expect(await screen.findByText(/Не удалось загрузить/)).toBeInTheDocument();
    // Two neutral «Цель» labels — the pulse row head and the quick
    // action — neither of which asserts anything about having a goal.
    expect(screen.getAllByText("Цель")).toHaveLength(2);
    // NEGATIVE (paired): neither of the two claims appears.
    expect(screen.queryByText("Выбери цель")).not.toBeInTheDocument();
    expect(screen.queryByText("Моя цель")).not.toBeInTheDocument();
    expect(screen.queryByText(/Цель не выбрана/)).not.toBeInTheDocument();
  });

  it("goal: name and week show, no percentage and no bar", async () => {
    serve(
      { ...BASE_TODAY, active_goals: [{ title: "Меньше стресса", week_num: 3 }] },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: goal and its real week are rendered.
    expect(await screen.findByText(/Меньше стресса/)).toBeInTheDocument();
    expect(screen.getByText(/3-я неделя/)).toBeInTheDocument();
    // NEGATIVE (paired): ни процента, ни шкалы ВНУТРИ строки цели.
    // Проценты калорий рядом — это другой ряд и другой факт.
    const goalRow = within(screen.getByLabelText(/^Цель: Меньше стресса/));
    expect(goalRow.queryByText(/%/)).not.toBeInTheDocument();
    expect(goalRow.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("goal progress is not drawn even if a percentage arrives (решение №13)", async () => {
    // Решение владельца №13 (06.09): на пилоте разрешён простой показ
    // «Моя цель» — без процентов, шкал и оценок выполнения. Раньше
    // полоса рисовалась, как только приходил `progress_pct`; бэкенд его
    // не слал, так что запрет держался на молчании источника. Теперь он
    // держится на экране, и лишнее поле в ответе ничего не рисует.
    serve(
      {
        ...BASE_TODAY,
        active_goals: [
          { title: "Меньше стресса", week_num: 3, progress_pct: 78 },
        ],
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: цель на месте — снят прогресс, а не сама цель.
    expect(await screen.findByText(/Меньше стресса/)).toBeInTheDocument();
    expect(screen.getByText("Моя цель")).toBeInTheDocument();
    // NEGATIVE (paired): процента и шкалы нет.
    expect(screen.queryByText(/78 %/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("progressbar", { name: /Меньше стресса/ }),
    ).not.toBeInTheDocument();
  });

  it("nothing on the home screen is weight, measurements, sleep or steps", async () => {
    // Решение владельца №10 (§35): вес, замеры, сон и шаги вне пилота.
    // Замер, а не утверждение: экран рисуется целиком и обыскивается.
    serve(
      { ...BASE_TODAY, active_goals: [{ title: "Меньше стресса", week_num: 3 }] },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: экран отрисован — иначе «ничего не нашли» ничего не значит.
    expect(await screen.findByText(/Меньше стресса/)).toBeInTheDocument();
    expect(screen.getByText(/3 \/ 8 стаканов/)).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    for (const forbidden of [
      "Вес",
      "вес,",
      "Замер",
      "замер",
      "Сон",
      "Шаг",
      "шаг",
      "Отзыв",
      "отзыв",
      "Рейтинг",
      "★",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });
});

describe("CustomerWellnessDashboardScreen — weekly rollup (DRF-1476)", () => {
  const TODAY = {
    calories_eaten: 777,
    calories_target: 1900,
    water_glasses_eaten: 3,
    water_glasses_target: 8,
    active_goals: [],
    display_name: "Анна",
  };

  function serve(activity: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? TODAY
          : u.includes("/recent-activity")
            ? activity
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    window.history.replaceState({}, "", "/customer/main");
  });

  it("rollup absent: Block 6 stays hidden, and invents no «0 из 7 дней»", async () => {
    serve({ this_week_booking_count: 0 });
    await renderScreen(false);

    // POSITIVE: the dashboard did render — so the absence below is the
    // gate working, not a blank screen.
    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    // NEGATIVE (paired): no fabricated week.
    expect(screen.queryByText(/Прогресс недели/)).not.toBeInTheDocument();
    expect(screen.queryByText(/из 7 дней/)).not.toBeInTheDocument();
  });

  it("rollup present and past the cold-start gate: Block 6 renders it", async () => {
    // The guard: proves Block 6 is hidden above for want of data, and
    // has not simply been removed.
    serve({
      this_week_booking_count: 0,
      weekly_progress: {
        water_days_logged: 4,
        food_days_logged: 5,
        active_days_count: 5,
      },
    });
    await renderScreen(false);

    expect(await screen.findByText(/Прогресс недели/)).toBeInTheDocument();
    expect(screen.getByText(/4 из 7 дней/)).toBeInTheDocument();
    expect(screen.getByText(/5 из 7 дней/)).toBeInTheDocument();
  });

  it("rollup present but below the cold-start gate: still hidden (§11.4)", async () => {
    serve({
      this_week_booking_count: 0,
      weekly_progress: {
        water_days_logged: 2,
        food_days_logged: 1,
        active_days_count: 2,
      },
    });
    await renderScreen(false);

    expect(await screen.findByText("Выбери цель")).toBeInTheDocument();
    expect(screen.queryByText(/Прогресс недели/)).not.toBeInTheDocument();
  });
});


/**
 * DRF-1546 — «не удалось прочитать» не то же самое, что «за день ничего
 * не залогировано».
 *
 * Бэкенд опускает срез, который не прочитался (`summary_known` /
 * `water_known` в `customer_wellness_today`), ровно как он уже опускает
 * `active_goals` и `weekly_progress`. Экран обязан сказать об этом
 * словами, а не нарисовать «0 / 0 ккал · 0 %» и пустые кружки — человек
 * с четырьмя выпитыми стаканами читал это как «ты ничего не пила».
 *
 * Стража парная: рядом с каждым «числа не выдуманы» стоит «числа
 * рисуются, когда они действительно пришли».
 */
describe("CustomerWellnessDashboardScreen — degraded reads (DRF-1546)", () => {
  function serve(today: unknown, activity: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? today
          : u.includes("/recent-activity")
            ? activity
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    window.history.replaceState({}, "", "/customer/main");
  });

  it("nutrition read failed: says so, invents no «0 / 0 ккал»", async () => {
    serve(
      {
        // calories_* + pfc отсутствуют — ручка не ответила.
        water_glasses_eaten: 4,
        water_glasses_target: 8,
        active_goals: [],
        display_name: "Анна",
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    // POSITIVE: соседний срез прочитался и рисуется как обычно.
    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.getByText("Не удалось загрузить")).toBeInTheDocument();
    // NEGATIVE (парная): ни нулей, ни процентов, ни «ничего не залогировано».
    expect(screen.queryByText(/ккал/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Ещё ничего не залогировано/),
    ).not.toBeInTheDocument();
  });

  it("hydration read failed: says so, and «+ стакан» still works", async () => {
    serve(
      {
        calories_eaten: 0,
        calories_target: 2100,
        // water_* отсутствуют.
        active_goals: [],
        display_name: "Анна",
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    expect(await screen.findByText("Не удалось загрузить")).toBeInTheDocument();
    // NEGATIVE: пустых кружков и «0 / 8 стаканов» нет.
    expect(screen.queryByText(/стаканов/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("progressbar", { name: /Вода/ }),
    ).not.toBeInTheDocument();
    // POSITIVE (парная): запись воды — отдельная ручка, она жива.
    expect(
      screen.getByRole("button", { name: "Добавить стакан воды 250 мл" }),
    ).toBeInTheDocument();
  });

  it("both reads OK: real numbers render, nothing says «не удалось»", async () => {
    // Парная положительная стража на обе проверки выше: правка, которая
    // прятала бы числа всегда, прошла бы их и упала здесь.
    serve(
      {
        calories_eaten: 1240,
        calories_target: 2100,
        pfc: { protein_g: 65, fat_g: 40, carbs_g: 120 },
        water_glasses_eaten: 4,
        water_glasses_target: 8,
        active_goals: [],
        display_name: "Анна",
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    expect(
      await screen.findByText(/1240 \/ 2100 ккал · 59 %/),
    ).toBeInTheDocument();
    expect(screen.getByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.getByText(/Б 65 · Ж 40 · У/)).toBeInTheDocument();
    expect(screen.queryByText("Не удалось загрузить")).not.toBeInTheDocument();
  });

  it("zero is still zero: an empty day reads as an empty day", async () => {
    // Ноль — настоящее значение и обязан рисоваться, иначе «опускаем
    // при сбое» превратилось бы в «прячем всегда».
    serve(
      {
        calories_eaten: 0,
        calories_target: 2100,
        water_glasses_eaten: 0,
        water_glasses_target: 8,
        active_goals: [],
        display_name: "Анна",
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    expect(
      await screen.findByText(/Ещё ничего не залогировано/),
    ).toBeInTheDocument();
    expect(screen.getByText(/0 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.queryByText("Не удалось загрузить")).not.toBeInTheDocument();
  });

  it("«Добрать белок» is gone even when a protein target arrives", async () => {
    // Строка снята: бэкенд `protein_target_g` не шлёт и источника не
    // имеет. Парная положительная стража — водяная строка «Цели
    // сегодня» на месте, то есть снят пункт, а не весь блок.
    serve(
      {
        calories_eaten: 800,
        calories_target: 2100,
        pfc: { protein_g: 65, fat_g: 40, carbs_g: 120, protein_target_g: 100 },
        water_glasses_eaten: 4,
        water_glasses_target: 8,
        active_goals: [],
        display_name: "Анна",
      },
      { this_week_booking_count: 0 },
    );
    await renderScreen(false);

    expect(await screen.findByText(/Ещё 4 стакана до цели/)).toBeInTheDocument();
    expect(screen.queryByText(/Добрать белок/)).not.toBeInTheDocument();
  });
});

/**
 * Норма воды: показываем только настоящую.
 *
 * До правки бэкенд подставлял `_WATER_GLASSES_TARGET_DEFAULT = 8`, когда
 * Ayla отвечает `norm_ml=0` — то есть когда нормы у человека нет вовсе
 * (анкету питания он не проходил). Экран рисовал «4 / 8 стаканов»,
 * восемь точек и «Ещё 4 стакана до цели»: чужое число как ЕГО дневную
 * цель, с процентом выполнения.
 *
 * Стража парная (`negative_assert_guard`, DRF-1411): к «цели и шкалы
 * нет» приложены положительные на тех же данных — выпитое видно
 * (`water_glasses_eaten` — настоящее число, скрывать его вместе с
 * выдумкой нельзя), соседняя строка питания цела, и второй случай
 * показывает, что С НАСТОЯЩЕЙ нормой цель, шкала и «до цели»
 * возвращаются.
 *
 * Тест умеет падать: верните `water_glasses_target: 8` в ответ ручки —
 * покраснеет первый случай; сделайте `waterKnown` снова зависимым от
 * цели — покраснеет положительная проверка про «2 стакана сегодня».
 */
describe("CustomerWellnessDashboardScreen — норма воды не выдумывается", () => {
  function serve(today: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? today
          : u.includes("/recent-activity")
            ? { this_week_booking_count: 0 }
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    window.history.replaceState({}, "", "/customer/main");
  });

  it("нормы нет: ни цели, ни шкалы, ни «до цели» — но выпитое видно", async () => {
    serve({
      calories_eaten: 800,
      calories_target: 2100,
      water_glasses_eaten: 2,
      // water_glasses_target отсутствует — Ayla ответила norm_ml=0.
      active_goals: [],
      display_name: "Анна",
    });
    await renderScreen(false);

    // POSITIVE: настоящее число на месте, и соседний срез цел.
    expect(await screen.findByText("2 стакана сегодня")).toBeInTheDocument();
    expect(screen.getByText(/800 \/ 2100 ккал/)).toBeInTheDocument();
    // NEGATIVE: выдуманной восьмёрки нет ни в числах, ни в шкале, ни в целях.
    expect(screen.queryByText(/\/ 8 стаканов/)).not.toBeInTheDocument();
    expect(screen.queryByText(/до цели/)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/из 8 стаканов/),
    ).not.toBeInTheDocument();
  });

  it("норма есть: цель, шкала и «до цели» возвращаются", async () => {
    serve({
      calories_eaten: 800,
      calories_target: 2100,
      water_glasses_eaten: 4,
      water_glasses_target: 8,
      active_goals: [],
      display_name: "Анна",
    });
    await renderScreen(false);

    expect(await screen.findByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    expect(screen.getByText(/Ещё 4 стакана до цели/)).toBeInTheDocument();
    expect(screen.queryByText(/стакана сегодня/)).not.toBeInTheDocument();
  });
});

describe("CustomerWellnessDashboardScreen — цель калорий не выдумывается", () => {
  function serve(today: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const u = String(url);
        const body = u.includes("/wellness/today")
          ? today
          : u.includes("/recent-activity")
            ? { this_week_booking_count: 0 }
            : null;
        if (body === null) throw new Error(`unexpected fetch: ${u}`);
        return { ok: true, status: 200, json: async () => body } as unknown as Response;
      }),
    );
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    mockedBrowse.mockResolvedValue({ services: [], masters: [], picks: [] });
    window.history.replaceState({}, "", "/customer/main");
  });

  it("цели нет: съеденное видно, а НЕДОСТУПНО не показывается", async () => {
    serve({
      calories_eaten: 800,
      // calories_target отсутствует — анкеты питания нет, Ayla ответила 0.
      // pfc тоже нет: строка БЖУ целевая (§11.1).
      water_glasses_eaten: 4,
      water_glasses_target: 8,
      active_goals: [],
      display_name: "Анна",
    });
    await renderScreen(false);

    // POSITIVE: съеденное на месте, соседний срез цел.
    expect(await screen.findByText("800 ккал сегодня")).toBeInTheDocument();
    expect(screen.getByText(/4 \/ 8 стаканов/)).toBeInTheDocument();
    // NEGATIVE: ни выдуманного знаменателя, ни процента, ни отказа.
    // «Не удалось загрузить» здесь было бы ЛОЖНЫМ отказом: чтение прошло,
    // это цели нет — и до правки экран показывал именно его, потому что
    // `caloriesKnown` требовал оба значения сразу.
    expect(screen.queryByText(/2000 ккал/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ккал · /)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Питание: .*недоступ/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Калории: /)).not.toBeInTheDocument();
  });

  it("цель есть: знаменатель, процент и шкала возвращаются", async () => {
    serve({
      calories_eaten: 800,
      calories_target: 2100,
      pfc: { protein_g: 65, fat_g: 40, carbs_g: 120 },
      water_glasses_eaten: 4,
      water_glasses_target: 8,
      active_goals: [],
      display_name: "Анна",
    });
    await renderScreen(false);

    expect(await screen.findByText(/800 \/ 2100 ккал/)).toBeInTheDocument();
    expect(screen.getByLabelText("Калории: 800 из 2100")).toBeInTheDocument();
    expect(screen.getByText(/Б 65 · Ж 40 · У 120 г/)).toBeInTheDocument();
    expect(screen.queryByText(/ккал сегодня/)).not.toBeInTheDocument();
  });

  it("пустой день: шкалы нет и для скринридера, а не только для глаза", async () => {
    serve({
      calories_eaten: 0,
      calories_target: 2100,
      water_glasses_eaten: 0,
      water_glasses_target: 8,
      active_goals: [],
      display_name: "Анна",
    });
    await renderScreen(false);

    // POSITIVE: экран действительно нарисован и говорит про пустой день.
    expect(await screen.findByText("Ещё ничего не залогировано")).toBeInTheDocument();
    // NEGATIVE: полоса при нуле не видна глазом, но `role="progressbar"`
    // озвучивал «Калории: 0 из 2100» — дефект доставался ровно тому, кто
    // не может проверить глазами.
    expect(screen.queryByLabelText("Калории: 0 из 2100")).not.toBeInTheDocument();
  });
});
