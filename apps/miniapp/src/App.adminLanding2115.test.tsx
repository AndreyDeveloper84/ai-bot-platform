/**
 * Посадка салонной админки на тройку пилота (DRF-2115, §50 п.5–6).
 *
 * Отменяет §47.1 «посадку не переключать» — решение владельца 19.09.2026.
 * Владелец и администратор входят сразу в «Сегодня»; в нижней панели —
 * ровно три: Сегодня | Расписание | Ayla, и это так на ВСЕХ экранах
 * админа (Команда, Услуги, Чаты, Настройки открываются из аватара, а не
 * из панели). Ресепшн — как была: «День» + «Команда» (§35).
 *
 * Старые адреса не удалены (DRF-1345 закрывается переездом вкладок, а не
 * их снятием): `/admin/team`, `/admin/services`, `/admin/internal-chat`,
 * `/admin/settings` открываются по прямым ссылкам.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./lib/identity", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/identity")>();
  return { ...original, channelIdentity: () => "identified" as const };
});

vi.mock("./lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/admin-api")>();
  return {
    ...original,
    getMe: vi.fn(),
    getSalonDay: vi.fn(),
    listMasters: vi.fn(),
    getAvailabilityRequests: vi.fn(),
    getHandoffQueue: vi.fn(),
    getSalonReadiness: vi.fn(),
  };
});

vi.mock("./lib/internal-chat-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/internal-chat-api")>();
  return {
    ...original,
    listAdminThreads: vi.fn(async () => ({ items: [], total_count: 0, offset: 0, limit: 50 })),
  };
});

import {
  getAvailabilityRequests,
  getHandoffQueue,
  getMe,
  getSalonDay,
  getSalonReadiness,
  listMasters,
  type MeResponse,
} from "./lib/admin-api";
import { SALON_PILOT_LABELS } from "./lib/salon-pilot";
import { avatarSheetItemsFor, AVATAR_SHEET_COPY } from "./lib/avatar-sheet";
import { App } from "./App";

const mockedGetMe = vi.mocked(getMe);
const mockedDay = vi.mocked(getSalonDay);
const mockedMasters = vi.mocked(listMasters);
const mockedRequests = vi.mocked(getAvailabilityRequests);
const mockedHandoff = vi.mocked(getHandoffQueue);
const mockedReadiness = vi.mocked(getSalonReadiness);

const BASE_ME: MeResponse = {
  user: { id: "u-1", name: "Ирина Петрова", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "receptionist",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: false,
  master_id: null,
  landing_path: "/admin/team",
};
const OWNER_ME: MeResponse = { ...BASE_ME, role: "owner", is_owner: true };
const ADMIN_ME: MeResponse = { ...BASE_ME, role: "admin", is_admin: true };
const RECEPTION_ME: MeResponse = { ...BASE_ME, is_receptionist: true };
const OWNER_MASTER_ME: MeResponse = { ...OWNER_ME, is_master: true, master_id: "m-1" };

const TRIO = [SALON_PILOT_LABELS.today, SALON_PILOT_LABELS.schedule, SALON_PILOT_LABELS.ayla];

function renderAppAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function tabLabels(): string[] {
  const bar = screen.getByRole("navigation", { name: "Основная навигация" });
  return Array.from(bar.querySelectorAll("button, a")).map(
    (b) => b.getAttribute("aria-label") ?? b.textContent?.trim() ?? "",
  );
}

async function openAvatarSheet() {
  await userEvent.click(await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger }));
  return screen.findByRole("dialog", { name: AVATAR_SHEET_COPY.title });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedDay.mockResolvedValue({
    date: "2026-09-19",
    timezone: "Europe/Moscow",
    summary: { total: 0, upcoming: 0, completed: 0, released: 0 },
    masters: [],
    orphan_visits: [],
  });
  mockedMasters.mockResolvedValue({ items: [], next_cursor: null, total_count: 0 });
  mockedRequests.mockResolvedValue({ items: [], next_cursor: null });
  mockedHandoff.mockResolvedValue({ waiting: 0, rows: [] });
});

describe("посадка (DRF-2115)", () => {
  it("владелец без адреса попадает в «Сегодня», в панели ровно три", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/");
    expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
    expect(tabLabels()).toEqual(TRIO);
  });

  it("администратор — туда же", async () => {
    mockedGetMe.mockResolvedValue(ADMIN_ME);
    renderAppAt("/");
    expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
    expect(tabLabels()).toEqual(TRIO);
  });

  it("ресепшн — как была: «День», две вкладки (§35)", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/");
    await waitFor(() => expect(mockedDay).toHaveBeenCalled());
    expect(tabLabels()).toEqual(["День", "Команда"]);
  });
});

describe("панель ровно три на всех экранах админа (DRF-2115)", () => {
  it.each(["/admin/team", "/admin/services", "/admin/internal-chat", "/admin/settings"])(
    "владелец на %s видит тройку пилота, а не пять вкладок моста",
    async (path) => {
      mockedGetMe.mockResolvedValue(OWNER_ME);
      renderAppAt(path);
      await waitFor(() => expect(tabLabels().length).toBeGreaterThan(0));
      expect(tabLabels()).toEqual(TRIO);
      for (const gone of ["День", "Команда", "Услуги", "Чаты", "Настройки", "Ещё", "Профиль"]) {
        expect(tabLabels()).not.toContain(gone);
      }
    },
  );
});

describe("аватар → лист (DRF-2115)", () => {
  it("на «Сегодня» аватар с инициалами открывает лист из четырёх групп", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    const sheet = await openAvatarSheet();
    expect(within(sheet).getByText(AVATAR_SHEET_COPY.manage)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.team })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.services })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.staffChats })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.settings })).toBeInTheDocument();
    // §33: экрана профиля у чистого владельца нет — пункта нет
    expect(within(sheet).queryByRole("button", { name: AVATAR_SHEET_COPY.profile })).toBeNull();
    expect(screen.getByRole("button", { name: AVATAR_SHEET_COPY.trigger })).toHaveTextContent("ИП");
  });

  it.each([
    ["team", "/admin/team", "Команда"],
    ["services", "/admin/services", "Услуги мастеров"],
    ["staffChats", "/admin/internal-chat", "Чаты"],
    ["settings", "/admin/settings", "Настройки"],
  ] as const)("пункт %s ведёт на %s", async (key, _path, heading) => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    const sheet = await openAvatarSheet();
    await userEvent.click(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY[key] }));
    expect(await screen.findByRole("heading", { name: new RegExp(heading) })).toBeInTheDocument();
    expect(tabLabels()).toEqual(TRIO);
  });

  it("владелец-мастер получает «Профиль» → /master/profile", async () => {
    mockedGetMe.mockResolvedValue(OWNER_MASTER_ME);
    renderAppAt("/admin/today");
    const sheet = await openAvatarSheet();
    expect(within(sheet).getByRole("button", { name: AVATAR_SHEET_COPY.profile })).toBeInTheDocument();
  });

  it("аватар есть на всех трёх экранах пилота", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    for (const path of ["/admin/schedule", "/admin/ayla"]) {
      const { unmount } = render(
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>,
      );
      expect(await screen.findByRole("button", { name: AVATAR_SHEET_COPY.trigger })).toBeInTheDocument();
      unmount();
    }
  });
});

describe("состав листа по роли (DRF-2115)", () => {
  const ALLOWED = new Set(["profile", "team", "services", "staffChats", "settings"]);

  it("у владельца и администратора — управление, чаты, настройки; ничего финансового", () => {
    for (const me of [OWNER_ME, ADMIN_ME]) {
      const keys = avatarSheetItemsFor(me).map((i) => i.key);
      expect(keys).toEqual(["team", "services", "staffChats", "settings"]);
      for (const k of keys) expect(ALLOWED.has(k)).toBe(true);
    }
  });

  it("«Профиль» только при is_master, первым", () => {
    expect(avatarSheetItemsFor(OWNER_MASTER_ME).map((i) => i.key)).toEqual([
      "profile",
      "team",
      "services",
      "staffChats",
      "settings",
    ]);
    expect(avatarSheetItemsFor(OWNER_MASTER_ME)[0]?.to).toBe("/master/profile");
  });

  it("ресепшн — пусто: у неё нет ни одного из этих разделов", () => {
    expect(avatarSheetItemsFor(RECEPTION_ME)).toEqual([]);
  });
});

describe("карточки на «Сегодня» (DRF-2115)", () => {
  it("«Диалоги — N ждут ответа» из handoff-queue и ведёт на экран очереди", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedHandoff.mockResolvedValue({
      waiting: 2,
      rows: [
        {
          task_id: "task-1",
          status: "open",
          age_minutes: 84,
          created_at: "2026-09-19T05:00:00Z",
          claimed: false,
          addressee: "",
          escalated: true,
        },
        {
          task_id: "task-2",
          status: "in_progress",
          age_minutes: 3,
          created_at: "2026-09-19T06:21:00Z",
          claimed: true,
          addressee: "karina",
          escalated: false,
        },
      ],
    });
    renderAppAt("/admin/today");
    const card = await screen.findByRole("link", { name: /Диалоги — 2 ждут ответа/ });
    await userEvent.click(card);
    expect(await screen.findByRole("heading", { name: /Диалоги/ })).toBeInTheDocument();
    expect(screen.getByText(/1 ч 24 мин/)).toBeInTheDocument();
    expect(screen.getByText(/karina/)).toBeInTheDocument();
    expect(screen.getByText(/эскалирована/i)).toBeInTheDocument();
  });

  it("«График — N заявок» считает ожидающие и ведёт на заявки", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedRequests.mockResolvedValue({
      items: [{ id: "r-1" }, { id: "r-2" }, { id: "r-3" }] as never,
      next_cursor: null,
    });
    renderAppAt("/admin/today");
    const card = await screen.findByRole("link", { name: /График — 3 заявки/ });
    expect(mockedRequests).toHaveBeenCalledWith(
      expect.objectContaining({ status: "pending" }),
      expect.anything(),
    );
    await userEvent.click(card);
    expect(await screen.findByRole("heading", { name: /Запросы на смену графика/ })).toBeInTheDocument();
  });

  it("ноль ждущих — карточка говорит «никто не ждёт», «Готовности» нет (§33)", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/today");
    expect(await screen.findByRole("link", { name: /Диалоги — никто не ждёт/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /График — заявок нет/ })).toBeInTheDocument();
    expect(screen.queryByText(/Готовность/)).toBeNull();
  });

  it("ручка очереди упала — карточка без числа, не ноль", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedHandoff.mockRejectedValue(new Error("down"));
    renderAppAt("/admin/today");
    expect(await screen.findByRole("link", { name: /Диалоги — не удалось посчитать/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Диалоги — никто не ждёт/ })).toBeNull();
  });
});

describe("старая посадка снята (DRF-2115)", () => {
  it("на «Настройках» больше нет кнопки «Открыть пилотную админку» — пилот и есть посадка", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    renderAppAt("/admin/settings");
    expect(await screen.findByRole("heading", { name: /Настройки/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть пилотную админку" })).toBeNull();
  });
});

describe("карточка «Готовность» на «Сегодня» (DRF-2117)", () => {
  it("«Готовность — N проблем» из admin/readiness и ведёт на список поимённо", async () => {
    mockedGetMe.mockResolvedValue(OWNER_ME);
    mockedReadiness.mockResolvedValue({
      ready: false,
      unknown: false,
      source_problem: null,
      checked_at: "2026-09-20T09:00:00+00:00",
      masters_total: 2,
      problems: [
        {
          master: { id: "m-1", name: "Анна" },
          code: "schedule_missing",
          text: "Анна — не настроен график",
          origin: "catalog",
        },
      ],
      limits: [],
    });
    renderAppAt("/admin/today");
    const card = await screen.findByRole("link", { name: /Готовность — 1 проблема/ });
    await userEvent.click(card);
    expect(await screen.findByRole("heading", { name: /Готовность/ })).toBeInTheDocument();
    expect(screen.getByText("Анна — не настроен график")).toBeInTheDocument();
    // Панель — та же тройка: экран списка живёт внутри пилота, не за его пределами.
    expect(tabLabels()).toEqual(TRIO);
  });

  it("ресепшну /admin/readiness закрыт, как и вся тройка", async () => {
    mockedGetMe.mockResolvedValue(RECEPTION_ME);
    renderAppAt("/admin/readiness");
    expect(
      await screen.findByText(/открыт владельцу и администратору салона/),
    ).toBeInTheDocument();
    expect(mockedReadiness).not.toHaveBeenCalled();
  });
});
