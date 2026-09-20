/**
 * М-6b (эпик DRF-2150): перепись остальных мастерских экранов на общий
 * `SystemState` (DRF-1181 п.10, словарь DRF-2157). Здесь — три экрана без
 * собственных тестовых стендов: «Клиенты», «Уведомления», онбординг по
 * приглашению. Остальные шесть — блоками в их файлах.
 *
 * Правило одно на всех (ruling §61 М-6 б/е): загрузка — общий скелет без
 * слов; первичная ошибка — «Не удалось загрузить <предмет экрана>» +
 * «Попробовать снова», повтор зовёт ручку снова; старых текстов нет.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterCustomers: vi.fn(),
    getNotificationPrefs: vi.fn(),
    claimInvite: vi.fn(),
  };
});

import { claimInvite, getMasterCustomers, getNotificationPrefs } from "../lib/master-api";
import { MasterCustomersScreen } from "./MasterCustomersScreen";
import { MasterNotificationSettingsScreen } from "./MasterNotificationSettingsScreen";
import { MasterOnboardingScreen } from "./MasterOnboardingScreen";

const mockedCustomers = vi.mocked(getMasterCustomers);
const mockedPrefs = vi.mocked(getNotificationPrefs);
const mockedClaim = vi.mocked(claimInvite);

const OLD_TEXTS = [
  /Не получилось загрузить/,
  /Загружаем /,
  /Проверьте интернет/,
  /Что-то у нас не получается/,
  /Нет связи/,
];

function expectNoOldTexts() {
  for (const re of OLD_TEXTS) expect(screen.queryByText(re)).toBeNull();
}

function renderAt(path: string, element: React.ReactElement, routePath = path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePath} element={element} />
        <Route path="*" element={<p>другой экран</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("«Клиенты» — SystemState", () => {
  it("загрузка — скелет без слов", () => {
    mockedCustomers.mockReturnValue(new Promise(() => {}));
    renderAt("/solo/customers", <MasterCustomersScreen />);
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
    expectNoOldTexts();
  });

  it("ошибка — «Не удалось загрузить клиентов» + «Попробовать снова», повтор зовёт ручку", async () => {
    mockedCustomers.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce([]);
    renderAt("/solo/customers", <MasterCustomersScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить клиентов");
    expectNoOldTexts();
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(mockedCustomers).toHaveBeenCalledTimes(2);
  });
});

describe("«Уведомления» — SystemState", () => {
  it("загрузка — скелет без слов", () => {
    mockedPrefs.mockReturnValue(new Promise(() => {}));
    renderAt("/master/settings/notifications", <MasterNotificationSettingsScreen />);
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
    expectNoOldTexts();
  });

  it("ошибка — «Не удалось загрузить настройки уведомлений» + «Попробовать снова»", async () => {
    mockedPrefs.mockRejectedValue(new Error("boom"));
    renderAt("/master/settings/notifications", <MasterNotificationSettingsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить настройки уведомлений",
    );
    expectNoOldTexts();
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(mockedPrefs).toHaveBeenCalledTimes(2);
  });
});

describe("онбординг по приглашению — SystemState", () => {
  it("загрузка — скелет без слов", () => {
    mockedClaim.mockReturnValue(new Promise(() => {}));
    renderAt("/onboarding/master?token=t-1", <MasterOnboardingScreen />, "/onboarding/master");
    expect(screen.getByRole("status", { busy: true })).toBeInTheDocument();
    expectNoOldTexts();
  });

  it("сеть/5xx — «Не удалось загрузить приглашение» + «Попробовать снова», повтор зовёт claim", async () => {
    mockedClaim.mockRejectedValue(new Error("boom"));
    renderAt("/onboarding/master?token=t-1", <MasterOnboardingScreen />, "/onboarding/master");
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить приглашение");
    expectNoOldTexts();
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(mockedClaim).toHaveBeenCalledTimes(2);
  });
});
