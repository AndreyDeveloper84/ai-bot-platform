/**
 * §83 — «График» на карточке мастера: три состояния, которые нельзя слить.
 *
 * Формулировки состояний берутся из `lib/schedule-confirmation-state` —
 * одного словаря на обе поверхности (пилотную и эту). Менять текст здесь
 * в одиночку нельзя: сторож `tools/lint/schedule_vocabulary_guard.py`
 * уронит второе имя того же состояния.
 *
 * Экран обязан различать «не подтверждено», «подтверждено для этих часов» и
 * «подтверждено, но часы с тех пор изменились». Третье выглядит как второе
 * ровно до тех пор, пока мастер не уходит с витрины — и тогда владелица не
 * понимает, почему.
 *
 * Плюс две границы: недоступный источник называется словами, а не рисуется
 * пустым расписанием, и кнопка недоступна тому, кто не владелец.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getMasterDetail: vi.fn(),
    getMasterAudit: vi.fn(),
    getMasterSchedule: vi.fn(),
    confirmMasterSchedule: vi.fn(),
  };
});

import {
  getMasterAudit,
  getMasterDetail,
  getMasterSchedule,
  type MasterDetail,
  type MasterSchedule,
  type MeResponse,
} from "../../lib/admin-api";
import { AdminMasterDetailScreen } from "./AdminMasterDetailScreen";

const mockedDetail = vi.mocked(getMasterDetail);
const mockedAudit = vi.mocked(getMasterAudit);
const mockedSchedule = vi.mocked(getMasterSchedule);

function me(isOwner: boolean): MeResponse {
  return {
    user: { id: "u-1", name: "Карина", phone_masked: "+7 *** **12" },
    tenant: { id: "t-1", name: "Формула тела", slug: "formula-tela" },
    role: isOwner ? "owner" : "admin",
    capabilities: [],
    is_customer: false,
    is_master: false,
    is_receptionist: false,
    is_admin: !isOwner,
    is_owner: isOwner,
    master_id: null,
    landing_path: "/admin/team",
  };
}

const MASTER: MasterDetail = {
  id: "m-2",
  name: "Тихонова Ольга",
  specialization: "",
  bio: "",
  experience: "",
  rating: null,
  is_active: true,
  invite_status: "accepted",
  mode: "invite",
  photo_url: "",
  max_handle: "",
  yclients_staff_id: null,
  invited_at: null,
  archived_at: null,
  linked_bot_user: null,
  services: [],
};

function schedule(overrides: Partial<MasterSchedule["confirmation"]> = {}): MasterSchedule {
  return {
    source: "ayla",
    days: Array.from({ length: 7 }, (_, i) => ({
      day_of_week: i,
      is_working_day: i !== 6,
      start_time: i !== 6 ? "10:00" : null,
      end_time: i !== 6 ? "19:00" : null,
      break_start: i === 0 ? "13:00" : null,
      break_end: i === 0 ? "14:00" : null,
    })),
    has_working_day: true,
    confirmation: {
      confirmed_at: null,
      confirmed_by: null,
      is_current: false,
      fingerprint: "v1:ayla:abc",
      block: null,
      ...overrides,
    },
  };
}

function renderScreen(isOwner = true) {
  return render(
    <MemoryRouter initialEntries={["/admin/team/m-2"]}>
      <Routes>
        <Route
          path="/admin/team/:masterId"
          element={<AdminMasterDetailScreen me={me(isOwner)} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("График на карточке мастера", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedDetail.mockResolvedValue(MASTER);
    mockedAudit.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("показывает часы вместе с перерывом — заверяют то, что видят", async () => {
    mockedSchedule.mockResolvedValue(schedule());

    renderScreen();

    // Шесть рабочих дней и один выходной — неделя показана целиком, а не
    // сведена в одну строку «Пн-Пт»: сводка теряет ровно то, что владелица
    // и должна проверить.
    expect(await screen.findAllByText("10:00–19:00")).toHaveLength(6);
    expect(screen.getAllByText("выходной")).toHaveLength(1);
    expect(screen.getByText(/перерыв 13:00–14:00/)).toBeTruthy();
  });

  it("неподтверждённое расписание названо словами владельца", async () => {
    mockedSchedule.mockResolvedValue(schedule());

    renderScreen();

    expect(await screen.findByText(/Расписание не подтверждено/)).toBeTruthy();
    expect(screen.getByText("Проверьте рабочие часы мастера")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Расписание верно" })).toBeTruthy();
  });

  it("подтверждённое для ЭТИХ часов показывает дату и не предлагает кнопку", async () => {
    mockedSchedule.mockResolvedValue(
      schedule({
        confirmed_at: "2026-09-09T10:00:00+00:00",
        confirmed_by: { id: "u-1", name: "Карина" },
        is_current: true,
      }),
    );

    renderScreen();

    expect(await screen.findByText(/Расписание подтверждено 9 сентября/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Расписание верно" })).toBeNull();
  });

  it("часы изменились после подтверждения — это НЕ «подтверждено»", async () => {
    // Столбец на месте, а «актуально» уже нет. Слей эти два состояния —
    // и мастер уйдёт с витрины без объяснения.
    mockedSchedule.mockResolvedValue(
      schedule({
        confirmed_at: "2026-09-09T10:00:00+00:00",
        confirmed_by: { id: "u-1", name: "Карина" },
        is_current: false,
      }),
    );

    renderScreen();

    expect(await screen.findByText(/Часы изменились после подтверждения/)).toBeTruthy();
    expect(screen.queryByText(/Расписание подтверждено 9 сентября/)).toBeNull();
    expect(screen.getByRole("button", { name: "Расписание верно" })).toBeTruthy();
  });

  it("без единого рабочего дня кнопка не нажимается и причина названа", async () => {
    const empty = schedule({ block: "no_working_day" });
    empty.days = empty.days.map((d) => ({
      ...d,
      is_working_day: false,
      start_time: null,
      end_time: null,
      break_start: null,
      break_end: null,
    }));
    empty.has_working_day = false;
    mockedSchedule.mockResolvedValue(empty);

    renderScreen();

    expect(
      await screen.findByText("В расписании нет ни одного рабочего дня — подтверждать нечего."),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Расписание верно" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("админу кнопка недоступна — подтверждает владелец", async () => {
    mockedSchedule.mockResolvedValue(schedule());

    renderScreen(false);

    const button = await screen.findByRole("button", { name: "Расписание верно" });
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("недоступный источник называется, а не рисуется пустым расписанием", async () => {
    // Пустой список читался бы как «мастер не работает никогда», и
    // владелица пошла бы чинить график, с которым всё в порядке.
    mockedSchedule.mockRejectedValue(new Error("boom"));

    renderScreen();

    await waitFor(() => {
      expect(screen.getByText(/расписание отдаёт другая система/)).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "Расписание верно" })).toBeNull();
  });
});
