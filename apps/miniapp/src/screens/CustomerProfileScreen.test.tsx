/**
 * Component tests for `CustomerProfileScreen` — pilot phase 1 baseline.
 *
 * The screen currently runs on DEV-only stubs (`customer-profile.ts`),
 * which resolve in-memory under vitest (`import.meta.env.DEV` === true),
 * so no network mocking is needed. Module state (consent stubs) is
 * per-module-instance — `vi.resetModules()` per test keeps them isolated.
 *
 * NOTE (pilot phase 2a): «Запросить данные» / «Удалить аккаунт» open the
 * in-app C5 sheets (`PersonalDataSheets.tsx`) wired to the frozen
 * 152-ФЗ endpoints; the sheets' own suite mocks `lib/personal-data`.
 * The support deeplink remains as the error-state fallback (#949) and
 * for the notifications preset.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

async function renderFresh() {
  vi.resetModules();
  const { CustomerProfileScreen } = await import("./CustomerProfileScreen");
  render(
    <MemoryRouter initialEntries={["/customer/profile"]}>
      <CustomerProfileScreen />
    </MemoryRouter>,
  );
}

describe("CustomerProfileScreen", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the stub profile header and privacy sections after loading", async () => {
    await renderFresh();
    expect(await screen.findByText("Анна Петрова")).toBeInTheDocument();
    expect(screen.getByText("@anna_petrova")).toBeInTheDocument();
    // Multi-tenant scope hint: 3 tenants → nearest + «+2 салона».
    expect(screen.getByText(/Клиент Beauty Place/)).toBeInTheDocument();
    expect(screen.getByText(/\+2 салона/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Согласия и приватность" }),
    ).toBeInTheDocument();
  });

  it("toggles the marketing consent switch and confirms via snackbar", async () => {
    const user = userEvent.setup();
    await renderFresh();
    const marketingSwitch = await screen.findByRole("switch", {
      name: "Получать акции и предложения от салонов",
    });
    expect(marketingSwitch).toHaveAttribute("aria-checked", "false");
    await user.click(marketingSwitch);
    await waitFor(() =>
      expect(marketingSwitch).toHaveAttribute("aria-checked", "true"),
    );
    expect(
      await screen.findByText(/буду показывать предложения от салонов/),
    ).toBeInTheDocument();
  });

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
  });

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
  });
});
