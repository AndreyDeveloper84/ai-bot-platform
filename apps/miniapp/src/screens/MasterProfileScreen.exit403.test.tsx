/**
 * Инцидент 20.09 (стенд, владелец): у мастера с непривязанной личностью
 * `/master/profile/card` и `/master/profile/portfolio` отвечают 403 — экран
 * профиля показывал только состояние ошибки, без секции «НАСТРОЙКИ» и без
 * панели. А «Выйти из аккаунта» / «Сменить режим» живут на /master/settings,
 * куда дверь — ⚙ «Настройки приложения ›» с этого экрана. Выхода с
 * поверхности мастера не было вообще.
 *
 * Правило класса (главное окно 20.09): SystemState forbidden/load_error на
 * мастерском экране НИКОГДА не убирает навигацию к настройкам. Здесь узел на
 * боевой форме: card 403 + portfolio 403 → ⚙ есть → /master/settings
 * открывается; 403 not_linked — доменный текст, не «Недостаточно прав».
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterMe: vi.fn(),
    getMasterProfileCard: vi.fn(),
    getPortfolio: vi.fn(),
  };
});

import { ApiError } from "../lib/api";
import { getMasterMe, getMasterProfileCard, getPortfolio } from "../lib/master-api";
import { MasterProfileScreen, PROFILE_COPY } from "./MasterProfileScreen";

const ME = {
  master: { id: "m-1", name: "Архипкин", specialization: "", bio: "", photo_url: "", services: [] },
  salon: { tenant_id: "t-1", name: "Формула тела" },
  permissions: { can_edit_schedule: false, can_edit_services: false, can_message_customers: false },
};

function mount(path = "/master/profile") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/master/profile" element={<MasterProfileScreen />} />
        <Route path="/solo/profile" element={<MasterProfileScreen />} />
        <Route path="/master/settings" element={<h1>Экран «Настройки» — Выйти из аккаунта</h1>} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(getMasterMe).mockReset().mockResolvedValue(ME);
  vi.mocked(getMasterProfileCard)
    .mockReset()
    .mockRejectedValue(new ApiError(403, "not_linked", "Профиль ещё не связан с каталогом."));
  vi.mocked(getPortfolio)
    .mockReset()
    .mockRejectedValue(new ApiError(403, "not_linked", "Профиль ещё не связан с каталогом."));
});

describe("профиль при card 403 + portfolio 403 — выход с поверхности остаётся", () => {
  it("⚙ «Настройки приложения ›» есть и ведёт на /master/settings", async () => {
    mount();
    const gear = await screen.findByRole("button", { name: PROFILE_COPY.buttons.appSettings });
    await userEvent.click(gear);
    expect(await screen.findByRole("heading", { name: /Выйти из аккаунта/ })).toBeInTheDocument();
  });

  it("панель Сегодня · Расписание · Ayla на месте", async () => {
    mount();
    await screen.findByRole("button", { name: PROFILE_COPY.buttons.appSettings });
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
  });

  it("403 not_linked — доменный текст про привязку, не «Недостаточно прав»", async () => {
    mount();
    await screen.findByRole("button", { name: PROFILE_COPY.buttons.appSettings });
    // Текст утверждён владельцем (DRF-2378). Предмет узла прежний: 403
    // говорит ДОМЕННЫМИ словами про состояние профиля, а не «Недостаточно
    // прав» — менялась формулировка, не правило.
    expect(screen.getByText(/Профиль пока не подключён/)).toBeInTheDocument();
    expect(screen.queryByText(/Недостаточно прав/)).toBeNull();
    expect(screen.queryByText(/Это действие недоступно/)).toBeNull();
  });

  it("сеть/5xx на карточке — общая ошибка с повтором, но настройки и панель всё равно есть", async () => {
    vi.mocked(getMasterProfileCard).mockRejectedValue(new Error("boom"));
    mount();
    await screen.findByRole("button", { name: PROFILE_COPY.buttons.appSettings });
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить профиль");
    expect(screen.getByRole("button", { name: "Попробовать снова" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
  });

  it("даже /me упал — ⚙ и панель есть (дверь к выходу не зависит от данных)", async () => {
    vi.mocked(getMasterMe).mockRejectedValue(new Error("boom"));
    mount();
    expect(await screen.findByRole("button", { name: PROFILE_COPY.buttons.appSettings })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
  });
});
