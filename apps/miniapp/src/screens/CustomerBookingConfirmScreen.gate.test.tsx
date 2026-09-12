/**
 * DRF-1319 B — гейт подтверждения записи открывается по ОДНОМУ определению.
 *
 * Экран больше не спрашивает `getInitData()` сам: состояние «канал не
 * передал initData» приходит из `lib/identity.ts::channelIdentity`. Этот
 * тест — положительная стража на то, что экран этому определению
 * подчиняется в обе стороны: пусто → гейт («Чтобы записаться») и ни
 * одного вызова записи; есть → обычная карточка.
 *
 * Что здесь НЕ проверяется: текст и поведение самого гейта — срез 1319-D,
 * заперт решением о MAX OAuth. Здесь важно только, КТО решает.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as identity from "../lib/identity";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

vi.mock("../lib/customer-booking", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-booking")>();
  return { ...original, createCustomerBooking: vi.fn() };
});

import { authVerify } from "../lib/api";
import { resetBooking, setMaster, setService, setVisitAt } from "../state/booking";

/** В будущем относительно часов теста (DRF-1776): прошедшее время экран
 * считает устаревшим подтверждением и прячет «Записаться». */
const FUTURE_VISIT = new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString();
import { createCustomerBooking } from "../lib/customer-booking";
import { CustomerBookingConfirmScreen } from "./CustomerBookingConfirmScreen";

const mockedAuthVerify = vi.mocked(authVerify);
const mockedCreate = vi.mocked(createCustomerBooking);

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/booking/confirm"]}>
      <Routes>
        <Route path="/customer/booking/confirm" element={<CustomerBookingConfirmScreen />} />
        <Route path="/customer/catalog" element={<div>CATALOG-PROBE</div>} />
        <Route path="/customer/masters/:masterId/slots" element={<div>SLOTS-PROBE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  mockedAuthVerify.mockResolvedValue({
    user: { id: "u-1", channel_user_id: "cu-1", display_name: "Ольга", client_name: "" },
    tenant: { slug: "demo", name: "Demo", timezone: "Europe/Moscow" },
    pending_booking_intent: null,
  });
  resetBooking();
  setService("svc-1", "Маникюр");
  setMaster("mst-1", "Анна Соколова");
  setVisitAt(FUTURE_VISIT);
});

describe("гейт решается lib/identity, не экраном", () => {
  it("no_init_data → гейт «Чтобы записаться», запись не создаётся", () => {
    vi.spyOn(identity, "channelIdentity").mockReturnValue("no_init_data");

    renderScreen();

    expect(screen.getByText("Чтобы записаться")).toBeTruthy();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("identified → обычная карточка подтверждения, гейта нет", () => {
    vi.spyOn(identity, "channelIdentity").mockReturnValue("identified");

    renderScreen();

    expect(screen.queryByText("Чтобы записаться")).toBeNull();
    expect(screen.getByText("Маникюр")).toBeTruthy();
  });
});
