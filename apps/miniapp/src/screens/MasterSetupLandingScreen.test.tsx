/**
 * Экран 01 «всё готово» (DRF-1807, M15) — по контракту readiness (M2).
 *
 * Сторожа:
 * - пункты — ровно из `items` сервера; `unavailable` РИСУЕТСЯ с причиной и
 *   без тапа (DRF-2326): спрятанный шаг мастер читает как «у меня всё», а
 *   отправить профиль всё равно не может — молчание хуже отказа;
 * - `unknown` — «не удалось прочитать», без «настройте» и без тапа;
 * - бар — по числу `done`, в тексте экрана нет ни `%`, ни «из N»;
 * - «Начать настройку» ведёт на deep_link первого незакрытого пункта,
 *   «Продолжить позже» — в «Мой день»;
 * - связь личности — отдельная строка, до LINKED слова «опубликован» нет;
 * - всё настроено — «Открыть кабинет».
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getOnboardingReadiness: vi.fn(),
    getMasterMe: vi.fn(),
  };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import {
  getMasterMe,
  getOnboardingReadiness,
  type OnboardingReadiness,
  type ReadinessItem,
} from "../lib/master-api";
import {
  ITEM_STATE_TEXT,
  LATER_LABEL,
  REASON_TEXT,
  MasterSetupLandingScreen,
  PUBLICATION_ROUTE,
  BAR_PARTLY_ELSEWHERE_TEXT,
  NOTHING_LEFT_HERE_TITLE,
  PUBLISH_ENTRY_LABEL,
  SETUP_EXPLAIN,
  SETUP_RESUME_NOTE,
  START_LABEL,
} from "./MasterSetupLandingScreen";

const mockedReadiness = vi.mocked(getOnboardingReadiness);
const mockedMe = vi.mocked(getMasterMe);

function item(key: string, state: string, extra: Partial<ReadinessItem> = {}): ReadinessItem {
  return {
    key,
    state,
    detail: {},
    reason: null,
    deep_link: `/solo/${key === "hours" ? "schedule" : key === "location" ? "settings" : key}`,
    ...extra,
  };
}

function readiness(items: ReadinessItem[], identity = "linked"): OnboardingReadiness {
  // DRF-2350: недоступный пункт отправку не держит — он в managed_elsewhere.
  const blocking = items
    .filter((i) => i.state !== "done" && i.state !== "unavailable")
    .map((i) => `${i.key}:${i.state}`);
  const managed_elsewhere = items
    .filter((i) => i.state === "unavailable")
    .map((i) => `${i.key}:${i.reason ?? i.state}`);
  return {
    ready: blocking.length === 0,
    blocking,
    items,
    managed_elsewhere,
    identity: { state: identity, link_status: null },
    setup_state: blocking.length === 0 ? "READY" : "SETUP_PENDING",
    sale_block: blocking.length === 0 ? null : "SETUP_PENDING",
  };
}

const FRESH = readiness([
  item("services", "missing"),
  item("location", "unavailable", { reason: "capability_not_built" }),
  item("hours", "missing"),
  item("profile", "missing"),
]);

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/solo/setup"]}>
      <Routes>
        <Route path="/solo/setup" element={<MasterSetupLandingScreen />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Без `\b`: перед кириллицей граница слова в JS не срабатывает. */
const COUNTER = /(^|\s)из \d/;

beforeEach(() => {
  vi.clearAllMocks();
  mockedMe.mockResolvedValue({
    master: {
      id: "m1",
      name: "Андрей",
      specialization: "",
      bio: "",
      photo_url: "",
      services: [],
    },
    salon: { tenant_id: "t1", name: "" },
    permissions: { can_edit_schedule: true, can_edit_services: false, can_message_customers: true },
  });
});

describe("экран 01", () => {
  it("приветствие по имени, пункты из readiness, unavailable виден с причиной", async () => {
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Андрей, всё готово 👋" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Осталось настроить" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      `○Услуги и цены${ITEM_STATE_TEXT.missing}`,
      `—Место работы${ITEM_STATE_TEXT.unavailable}${REASON_TEXT.capability_not_built}`,
      `○Расписание${ITEM_STATE_TEXT.missing}`,
      `○Профиль для клиентов${ITEM_STATE_TEXT.missing}`,
    ]);
    expect(screen.getByText(SETUP_RESUME_NOTE)).toBeInTheDocument();
  });

  it("недоступный пункт не тапается — даже когда сервер прислал ссылку", async () => {
    // У location в FRESH deep_link НЕ пуст намеренно: проверяем поведение,
    // а не форму. Иначе `onClick` на том же div прошёл бы обе проверки и
    // открыл ровно ту дверь, которую тикет открывать запрещает.
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    const row = await screen.findByTestId("setup-item-location");
    expect(row.tagName).not.toBe("BUTTON");
    expect(within(row).queryByRole("button")).toBeNull();
    fireEvent.click(row);
    expect(screen.queryByTestId("location")).toBeNull();
  });

  it("причина «ведётся не здесь» названа своим текстом, чужой причины — нет", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "unavailable", { reason: "managed_outside_app", deep_link: null }),
        item("hours", "missing"),
        item("profile", "missing"),
      ]),
    );
    renderScreen();
    const row = await screen.findByTestId("setup-item-services");
    expect(row.textContent).toContain(REASON_TEXT.managed_outside_app);
    expect(row.textContent).not.toContain(REASON_TEXT.capability_not_built);
  });

  it("причина, которой экран не знает: состояние названо, выдумки нет", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "unavailable", { reason: "нечто_новое", deep_link: null }),
        item("hours", "missing"),
        item("profile", "missing"),
      ]),
    );
    renderScreen();
    const row = await screen.findByTestId("setup-item-services");
    expect(row.textContent).toContain(ITEM_STATE_TEXT.unavailable);
    expect(row.textContent).not.toContain("нечто_новое");
  });

  it("причины нет вовсе: пункт называет состояние и молчит о причине", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "unavailable", { reason: null, deep_link: null }),
        item("hours", "missing"),
      ]),
    );
    renderScreen();
    const row = await screen.findByTestId("setup-item-services");
    expect(row.textContent).toContain(ITEM_STATE_TEXT.unavailable);
    expect(row.textContent).toBe(`—Услуги и цены${ITEM_STATE_TEXT.unavailable}`);
  });

  it("салонный мастер: оба пункта названы, и профиль можно отправить", async () => {
    // ПЕРЕВЁРНУТО DRF-2350 (§77 п. 1, решение владельца 23.09.2026).
    // Здесь был ЗАМЕР тупика: полный бар, ни одной кнопки действия и ни слова
    // о том, почему профиль не отправить. Замер был верным и на него владелец
    // и отвечал — блокировку снять. Узел не удалён: он показывает, что тупик
    // был записан и отменён решением, а не размыт правкой.
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "unavailable", { reason: "managed_outside_app", deep_link: null }),
        item("location", "unavailable", { reason: "managed_outside_app", deep_link: null }),
        item("hours", "done"),
        item("profile", "done"),
      ]),
    );
    renderScreen();
    const list = await screen.findByRole("list", { name: "Осталось настроить" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    expect(within(list).getAllByText(REASON_TEXT.managed_outside_app)).toHaveLength(2);
    const bar = screen.getByTestId("setup-bar");
    expect(bar).toHaveAttribute("aria-valuemax", "2");
    expect(bar).toHaveAttribute("aria-valuenow", "2");
    // Весь набор кнопок, а не отсутствие одной подписи: при fill.done > 0
    // кнопка звалась бы «Продолжить настройку», и проверка на START_LABEL не
    // могла бы упасть — ровно та вакуумность, против которой этот узел.
    const actions = screen.getAllByRole("button").filter((b) => !list.contains(b));
    expect(actions.map((b) => b.textContent)).toEqual([
      PUBLISH_ENTRY_LABEL,
      "Открыть кабинет",
    ]);
    // И слова больше не лгут в другую сторону: «Всё настроено» неправда, когда
    // два шага ведутся не здесь, — заголовок называет то, что верно.
    expect(screen.getByRole("heading", { name: NOTHING_LEFT_HERE_TITLE })).toBeInTheDocument();
    expect(screen.queryByText(SETUP_EXPLAIN)).toBeNull();
  });

  it("полоса объявляется словами, когда часть шагов ведётся не здесь", async () => {
    // Незрячий слышал «сто процентов» при двух недоступных пунктах: у полосы
    // знаменатель — достижимые шаги, и диктор читает долю, а не положение дел.
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "unavailable", { reason: "managed_outside_app", deep_link: null }),
        item("location", "unavailable", { reason: "managed_outside_app", deep_link: null }),
        item("hours", "done"),
        item("profile", "done"),
      ]),
    );
    renderScreen();
    expect(await screen.findByTestId("setup-bar")).toHaveAttribute(
      "aria-valuetext",
      BAR_PARTLY_ELSEWHERE_TEXT,
    );
  });

  it("пока настроено не всё, полоса молчит: объявить «готово» было бы ложью", async () => {
    // Недоступный пункт есть у КАЖДОГО мастера (место работы), поэтому
    // условие только по нему объявляло бы «настроено всё, что настраивается
    // здесь» на первом же visit'е при нуле закрытых шагов. `aria-valuetext`
    // не дополняет число, а ЗАМЕНЯЕТ его — соврал бы вместо «ноль процентов».
    mockedReadiness.mockResolvedValue(
      readiness([
        item("location", "unavailable", { reason: "capability_not_built", deep_link: null }),
        item("hours", "missing"),
        item("profile", "missing"),
      ]),
    );
    renderScreen();
    expect(await screen.findByTestId("setup-bar")).not.toHaveAttribute("aria-valuetext");
  });

  it("когда недоступных шагов нет, полоса ничего лишнего не объявляет", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "missing")]),
    );
    renderScreen();
    expect(await screen.findByTestId("setup-bar")).not.toHaveAttribute("aria-valuetext");
  });

  it("недоступный пункт не становится следующим шагом", async () => {
    // Недоступный пункт стоит ПЕРВЫМ: иначе «первый незакрытый» совпал бы с
    // верным ответом и без отбора, и узел ничего бы не держал.
    mockedReadiness.mockResolvedValue(
      readiness([
        item("location", "unavailable", { reason: "capability_not_built", deep_link: null }),
        item("services", "missing"),
        item("hours", "missing"),
      ]),
    );
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: START_LABEL }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/solo/services");
  });

  it("бар считает только то, что мастер может закрыть сам", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "done"),
        item("location", "unavailable", { reason: "capability_not_built", deep_link: null }),
        item("hours", "missing"),
      ]),
    );
    renderScreen();
    const bar = await screen.findByTestId("setup-bar");
    expect(bar).toHaveAttribute("aria-valuemax", "2");
    expect(bar).toHaveAttribute("aria-valuenow", "1");
  });

  it("в тексте экрана нет процентов и «из N»; бар — по числу done", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "missing"), item("profile", "done")]),
    );
    renderScreen();
    await screen.findByRole("heading", { level: 1 });
    const text = document.body.textContent ?? "";
    // Положительная стража: числа на экране есть (бар), слов о них — нет.
    const bar = screen.getByTestId("setup-bar");
    expect(bar).toHaveAttribute("aria-valuenow", "2");
    expect(bar).toHaveAttribute("aria-valuemax", "3");
    expect(text).not.toMatch(/%/);
    expect(text).not.toMatch(COUNTER);
  });

  it("unknown — «не удалось прочитать», не «настройте», и не кнопка", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([
        item("services", "done"),
        item("hours", "unknown", { reason: "SalonUnavailable" }),
        item("profile", "done"),
      ]),
    );
    renderScreen();
    const row = await screen.findByTestId("setup-item-hours");
    expect(row).toHaveTextContent(ITEM_STATE_TEXT.unknown);
    expect(row.tagName).toBe("DIV");
    expect(document.body.textContent).not.toMatch(/настройте/i);
  });

  it("«Начать настройку» ведёт на deep_link первого незакрытого; «Продолжить позже» — в «Мой день»", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "missing"), item("profile", "missing")]),
    );
    renderScreen();
    // done есть — подпись «Продолжить настройку», не «Начать».
    const cta = await screen.findByRole("button", { name: "Продолжить настройку" });
    fireEvent.click(cta);
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/schedule");
  });

  it("свежее пространство: «Начать настройку»; «Продолжить позже» уводит в кабинет", async () => {
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    await screen.findByRole("button", { name: START_LABEL });
    fireEvent.click(screen.getByRole("button", { name: LATER_LABEL }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/my-day");
  });

  it("тап по пункту ведёт по его deep_link", async () => {
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    fireEvent.click(await screen.findByTestId("setup-item-profile"));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/profile");
  });

  it.each([
    ["pending", "ожидает оператора"],
    ["unlinked", "после подтверждения личности"],
    ["rejected", "отклонено"],
  ])("связь личности %s — отдельная строка, без слова «опубликован»", async (state, fragment) => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "done"), item("profile", "done")], state),
    );
    renderScreen();
    const note = await screen.findByTestId("setup-identity");
    expect(note).toHaveTextContent(fragment);
    expect(document.body.textContent).not.toMatch(/опубликован/i);
  });

  it("всё настроено и связь есть — «Всё настроено», «Открыть кабинет», строки о личности нет", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "done"), item("profile", "done")]),
    );
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Всё настроено" })).toBeInTheDocument();
    expect(screen.queryByTestId("setup-identity")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Открыть кабинет" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/solo/my-day");
  });

  it("готово по серверу — главная кнопка ведёт на экран 08 отправки на проверку (M26)", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "done"), item("profile", "done")]),
    );
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: PUBLISH_ENTRY_LABEL }));
    expect(screen.getByTestId("location")).toHaveTextContent(PUBLICATION_ROUTE);
  });

  it("готово, но личность не подтверждена — кнопки отправки нет, строка о личности есть (M26)", async () => {
    mockedReadiness.mockResolvedValue(
      readiness([item("services", "done"), item("hours", "done"), item("profile", "done")], "unlinked"),
    );
    renderScreen();
    expect(await screen.findByTestId("setup-identity")).toHaveTextContent("после подтверждения личности");
    expect(screen.queryByRole("button", { name: PUBLISH_ENTRY_LABEL })).toBeNull();
  });

  it("не готово — кнопки отправки на проверку нет", async () => {
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    expect(await screen.findByRole("button", { name: START_LABEL })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: PUBLISH_ENTRY_LABEL })).toBeNull();
  });

  it("без имени экран всё равно рисуется", async () => {
    mockedMe.mockRejectedValue(new Error("no me"));
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Всё готово 👋" })).toBeInTheDocument();
  });
});

describe("системные состояния через SystemState (DRF-2194)", () => {
  it("загрузка — общий скелет без слов", () => {
    mockedReadiness.mockReturnValue(new Promise(() => {}));
    renderScreen();
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
  });

  it("ошибка — «Не удалось загрузить чек-лист настройки» + «Попробовать снова»", async () => {
    // DRF-2204 — the retry needs its own answer. Without it the second call
    // returned whatever the previous test left (or nothing), and the screen
    // crashed on `readiness.items` after the test had already passed.
    mockedReadiness.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(FRESH);
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить чек-лист настройки");
    expect(screen.queryByText(/Не получилось загрузить/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() => expect(mockedReadiness).toHaveBeenCalledTimes(2));
  });
});
