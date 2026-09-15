/**
 * DRF-1845 — «Принимаю записи» на дашборде мастера.
 *
 * Заперто: переключатель рисуется только по прочитанному из каталога (отказ
 * чтения — его нет); состояние и подсказка — из ответа, а не из нажатия;
 * пауза говорит про 15 минут и про уже созданные записи; неопубликованный
 * профиль называется своим текстом, прочие отказы — общим, переключатель при
 * отказе не меняет положение.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, getAcceptingBookings: vi.fn(), setAcceptingBookings: vi.fn() };
});

import { ApiError } from "../lib/api";
import { getAcceptingBookings, setAcceptingBookings } from "../lib/master-api";
import { ACCEPTING_COPY, AcceptingBookingsToggle } from "./AcceptingBookingsToggle";

const mockedGet = vi.mocked(getAcceptingBookings);
const mockedSet = vi.mocked(setAcceptingBookings);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AcceptingBookingsToggle", () => {
  it("не рисуется, если прочитать не удалось", async () => {
    mockedGet.mockRejectedValue(new ApiError(403, "not_linked", "…"));
    const { container } = render(<AcceptingBookingsToggle />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("рисует прочитанное: принимаю", async () => {
    mockedGet.mockResolvedValue({ accepting_bookings: true, status: "active" });
    render(<AcceptingBookingsToggle />);
    const sw = await screen.findByRole("switch", { name: ACCEPTING_COPY.label });
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(ACCEPTING_COPY.onHint)).toBeInTheDocument();
  });

  it("пауза: шлёт false и рисует ответ каталога с честным временем", async () => {
    const user = userEvent.setup();
    mockedGet.mockResolvedValue({ accepting_bookings: true, status: "active" });
    mockedSet.mockResolvedValue({ accepting_bookings: false, status: "active" });
    render(<AcceptingBookingsToggle />);

    await user.click(await screen.findByRole("switch", { name: ACCEPTING_COPY.label }));

    expect(mockedSet).toHaveBeenCalledWith(false);
    expect(await screen.findByText(ACCEPTING_COPY.offHint)).toBeInTheDocument();
    expect(ACCEPTING_COPY.offHint).toMatch(/15 минут/);
    expect(ACCEPTING_COPY.offHint).toMatch(/уже созданные записи остаются/);
    expect(screen.getByRole("switch", { name: ACCEPTING_COPY.label })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("неопубликованный профиль — свой текст, положение не меняется", async () => {
    const user = userEvent.setup();
    mockedGet.mockResolvedValue({ accepting_bookings: false, status: "draft" });
    mockedSet.mockRejectedValue(new ApiError(409, "profile_not_active", "…"));
    render(<AcceptingBookingsToggle />);

    await user.click(await screen.findByRole("switch", { name: ACCEPTING_COPY.label }));

    expect(await screen.findByText(ACCEPTING_COPY.notPublished)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: ACCEPTING_COPY.label })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("прочий отказ — общий текст", async () => {
    const user = userEvent.setup();
    mockedGet.mockResolvedValue({ accepting_bookings: true, status: "active" });
    mockedSet.mockRejectedValue(new ApiError(503, "accepting_bookings_unavailable", "…"));
    render(<AcceptingBookingsToggle />);

    await user.click(await screen.findByRole("switch", { name: ACCEPTING_COPY.label }));

    expect(await screen.findByText(ACCEPTING_COPY.failed)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: ACCEPTING_COPY.label })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
