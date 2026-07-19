/**
 * Component tests for `CustomerProfileScreen` — pilot phase 1 baseline.
 *
 * The screen currently runs on DEV-only stubs (`customer-profile.ts`),
 * which resolve in-memory under vitest (`import.meta.env.DEV` === true),
 * so no network mocking is needed. Module state (consent stubs) is
 * per-module-instance — `vi.resetModules()` per test keeps them isolated.
 *
 * NOTE (pilot phase 2a): the «Запросить данные» / «Удалить аккаунт»
 * buttons currently route to support deeplink sheets (deferred Variant 3).
 * When the C5 personal-data endpoints are wired, this suite is updated to
 * the real export/delete sheets — keep the support-route assertions until
 * then so the swap is a deliberate, reviewed change.
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

  it("opens the export support sheet with the support deeplink (phase 1 routing)", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Запросить данные" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByText("Нужна копия твоих данных?"),
    ).toBeInTheDocument();
    const deeplink = screen.getByRole("link", { name: "Написать в поддержку" });
    expect(deeplink).toHaveAttribute("href", "https://max.me/aylasupport");
    expect(deeplink).toHaveAttribute("target", "_blank");
  });

  it("opens the delete support sheet and closes it on Escape", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Удалить аккаунт" }),
    );
    expect(
      await screen.findByText("Хочешь удалить аккаунт?"),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});
