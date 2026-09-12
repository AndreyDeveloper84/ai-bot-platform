/**
 * Экран 01 «всё готово» (DRF-1807, M15) — по контракту readiness (M2).
 *
 * Сторожа:
 * - пункты — ровно из `items` сервера, `unavailable` не рисуется;
 * - `unknown` — «не удалось прочитать», без «настройте» и без тапа;
 * - бар — по числу `done`, в тексте экрана нет ни `%`, ни «из N»;
 * - «Начать настройку» ведёт на deep_link первого незакрытого пункта,
 *   «Продолжить позже» — в «Мой день»;
 * - связь личности — отдельная строка, до LINKED слова «опубликован» нет;
 * - всё настроено — «Открыть кабинет».
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
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
  MasterSetupLandingScreen,
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
  const blocking = items
    .filter((i) => i.state !== "done")
    .map((i) => `${i.key}:${i.state}`);
  return {
    ready: blocking.length === 0,
    blocking,
    items,
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
  it("приветствие по имени, пункты из readiness, unavailable не рисуется", async () => {
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Андрей, всё готово 👋" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Осталось настроить" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      `○Услуги и цены${ITEM_STATE_TEXT.missing}`,
      `○Расписание${ITEM_STATE_TEXT.missing}`,
      `○Профиль для клиентов${ITEM_STATE_TEXT.missing}`,
    ]);
    expect(screen.queryByText("Место работы")).toBeNull();
    expect(screen.getByText(SETUP_RESUME_NOTE)).toBeInTheDocument();
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

  it("без имени экран всё равно рисуется", async () => {
    mockedMe.mockRejectedValue(new Error("no me"));
    mockedReadiness.mockResolvedValue(FRESH);
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Всё готово 👋" })).toBeInTheDocument();
  });
});
