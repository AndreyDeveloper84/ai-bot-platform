/**
 * Component tests for `CustomerProfileScreen` — real-API baseline
 * (DRF-1475, часть Б, решение владельца 05.09).
 *
 * Экран читает/пишет настоящий `/customer/me` через
 * `customer-profile.ts` → `lib/api.ts`; request-слой мокируется на
 * границе модуля (`fetchProfile` / `updateProfile`). Скрытые до
 * DRF-1520 секции («Подсказки от Ayla», «Хранение данных») проверяются
 * на ОТСУТСТВИЕ в DOM с положительной стражей: секция маркетингового
 * согласия при этом обязана быть (DRF-1411).
 *
 * NOTE: «Запросить данные» / «Удалить аккаунт» открывают in-app C5
 * sheets (`PersonalDataSheets.tsx`), чей собственный suite мокирует
 * `lib/personal-data`.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchProfile, updateProfile, type Profile } from "../lib/api";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchProfile: vi.fn(),
    updateProfile: vi.fn(),
  };
});

const fetchProfileMock = vi.mocked(fetchProfile);
const updateProfileMock = vi.mocked(updateProfile);

function profileFixture(overrides: Partial<Profile> = {}): Profile {
  return {
    bot_user_id: "u-1",
    display_name: "Анна Петрова",
    client_name: "Аня",
    phone_masked: "+7 ••• ••• 45 67",
    timezone: "Europe/Moscow",
    joined_at: "2026-05-14T10:30:00+03:00",
    preferences: {
      notify_reminders: true,
      notify_retention: true,
      notify_promo: false,
      notify_birthday: false,
      birthday_date: null,
    },
    favorites: { master_name: null, service_name: null },
    ...overrides,
  };
}

function withPromo(p: Profile, notifyPromo: boolean): Profile {
  return { ...p, preferences: { ...p.preferences, notify_promo: notifyPromo } };
}

async function renderFresh() {
  vi.resetModules();
  const { CustomerProfileScreen } = await import("./CustomerProfileScreen");
  render(
    <MemoryRouter initialEntries={["/customer/profile"]}>
      <CustomerProfileScreen />
    </MemoryRouter>,
  );
}

/**
 * Render with `import.meta.env.DEV === false` — the production build
 * shape. The flag is read at module scope, so stub the env BEFORE the
 * dynamic import (vi.resetModules keeps module instances isolated).
 */
async function renderFreshProd() {
  vi.resetModules();
  vi.stubEnv("DEV", false);
  try {
    const { CustomerProfileScreen } = await import("./CustomerProfileScreen");
    render(
      <MemoryRouter initialEntries={["/customer/profile"]}>
        <CustomerProfileScreen />
      </MemoryRouter>,
    );
  } finally {
    vi.unstubAllEnvs();
  }
}

describe("CustomerProfileScreen (реальный /customer/me)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    fetchProfileMock.mockResolvedValue(profileFixture());
  }, 15000);

  it("renders the real name and the marketing section after loading", async () => {
    await renderFresh();
    // Положительная стража: имя из реального /me (client_name), секция
    // согласий и маркетинговый тумблер на месте (DRF-1411).
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Согласия и приватность" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("switch", {
        name: "Получать акции и предложения от салонов",
      }),
    ).toHaveAttribute("aria-checked", "false");
  }, 15000);

  it("hidden-until-DRF-1520 sections are absent from the DOM", async () => {
    await renderFresh();
    // Дождаться готового состояния, иначе отсутствие — артефакт loading.
    await screen.findByText("Аня");
    expect(
      screen.queryByRole("heading", { name: /Подсказки от/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Хранение данных")).not.toBeInTheDocument();
    expect(screen.queryByText(/Согласие дано/)).not.toBeInTheDocument();
    // Положительная стража: согласия и маркетинг при этом видны.
    expect(
      screen.getByRole("heading", { name: "Согласия и приватность" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("switch", {
        name: "Получать акции и предложения от салонов",
      }),
    ).toBeInTheDocument();
  }, 15000);

  it("выдача согласия: toggle шлёт PATCH notify_promo=true и обновляет UI", async () => {
    const user = userEvent.setup();
    updateProfileMock.mockImplementation(async (patch) =>
      withPromo(profileFixture(), patch.notify_promo ?? false),
    );
    await renderFresh();
    const marketingSwitch = await screen.findByRole("switch", {
      name: "Получать акции и предложения от салонов",
    });
    expect(marketingSwitch).toHaveAttribute("aria-checked", "false");
    await user.click(marketingSwitch);
    await waitFor(() =>
      expect(updateProfileMock).toHaveBeenCalledWith({ notify_promo: true }),
    );
    await waitFor(() =>
      expect(marketingSwitch).toHaveAttribute("aria-checked", "true"),
    );
    expect(
      await screen.findByText(/буду показывать предложения от салонов/),
    ).toBeInTheDocument();
  }, 15000);

  it("отзыв согласия: toggle шлёт PATCH notify_promo=false и обновляет UI", async () => {
    const user = userEvent.setup();
    fetchProfileMock.mockResolvedValue(withPromo(profileFixture(), true));
    updateProfileMock.mockImplementation(async (patch) =>
      withPromo(profileFixture(), patch.notify_promo ?? true),
    );
    await renderFresh();
    const marketingSwitch = await screen.findByRole("switch", {
      name: "Получать акции и предложения от салонов",
    });
    expect(marketingSwitch).toHaveAttribute("aria-checked", "true");
    await user.click(marketingSwitch);
    await waitFor(() =>
      expect(updateProfileMock).toHaveBeenCalledWith({ notify_promo: false }),
    );
    await waitFor(() =>
      expect(marketingSwitch).toHaveAttribute("aria-checked", "false"),
    );
    expect(
      await screen.findByText(/предложений от салонов больше не будет/),
    ).toBeInTheDocument();
  }, 15000);

  it("ошибка сохранения показывается честно и не переворачивает тумблер", async () => {
    const user = userEvent.setup();
    updateProfileMock.mockRejectedValue(new Error("network down"));
    await renderFresh();
    const marketingSwitch = await screen.findByRole("switch", {
      name: "Получать акции и предложения от салонов",
    });
    await user.click(marketingSwitch);
    expect(
      await screen.findByText(/Не получилось сохранить/),
    ).toBeInTheDocument();
    expect(marketingSwitch).toHaveAttribute("aria-checked", "false");
  }, 15000);

  it("opens the in-app C5 export sheet from «Запросить данные»", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Запросить данные" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Скачать мои данные")).toBeInTheDocument();
    expect(screen.getByText(/один файл/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Скачать данные" }),
    ).toBeInTheDocument();
  }, 15000);

  it("opens the in-app C5 delete sheet and closes it on Escape", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Удалить аккаунт" }),
    );
    expect(await screen.findByText("Удалить мои данные?")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  }, 15000);
});

/**
 * Production build shape (`import.meta.env.DEV === false`): имя и
 * маркетинговое согласие обязаны рисоваться из реального /me и в
 * проде (это и есть суть DRF-1475 части Б), а скрытые до DRF-1520
 * секции отсутствуют и здесь. Правило (владелец 05.09): не показывать
 * неработающие тумблеры.
 */
describe("CustomerProfileScreen (prod build)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    fetchProfileMock.mockResolvedValue(profileFixture());
  }, 15000);

  it("renders the real name and marketing consent in prod too", async () => {
    await renderFreshProd();
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", {
        name: "Получать акции и предложения от салонов",
      }),
    ).toBeInTheDocument();
    // Скрытые секции отсутствуют и в проде.
    expect(
      screen.queryByRole("heading", { name: /Подсказки от/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Хранение данных")).not.toBeInTheDocument();
    // Никакой выдуманной личности и никакого StateError от stub-гарды.
    expect(screen.queryByText("Анна Петрова")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Профиль ещё не подключён/),
    ).not.toBeInTheDocument();
  }, 15000);

  it("prod marketing toggle шлёт реальный PATCH", async () => {
    const user = userEvent.setup();
    updateProfileMock.mockImplementation(async (patch) =>
      withPromo(profileFixture(), patch.notify_promo ?? false),
    );
    await renderFreshProd();
    const marketingSwitch = await screen.findByRole("switch", {
      name: "Получать акции и предложения от салонов",
    });
    await user.click(marketingSwitch);
    await waitFor(() =>
      expect(updateProfileMock).toHaveBeenCalledWith({ notify_promo: true }),
    );
  }, 15000);

  it("opens the in-app C5 export sheet in the prod build", async () => {
    const user = userEvent.setup();
    await renderFreshProd();
    await user.click(
      await screen.findByRole("button", { name: "Запросить данные" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Скачать мои данные")).toBeInTheDocument();
  }, 15000);

  it("opens the in-app C5 delete sheet in the prod build", async () => {
    const user = userEvent.setup();
    await renderFreshProd();
    await user.click(
      await screen.findByRole("button", { name: "Удалить аккаунт" }),
    );
    expect(await screen.findByText("Удалить мои данные?")).toBeInTheDocument();
  }, 15000);
});
