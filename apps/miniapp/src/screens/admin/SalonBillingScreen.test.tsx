/**
 * Подписка салона (DRF-2988, B3b) — экран владельца и администратора.
 *
 * Каждое отрицание идёт в паре с присутствием на тех же данных (DRF-1411):
 * «кнопки отмены нет у админа» стоит рядом с «у владельца она есть».
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getSalonBillingStatus: vi.fn(),
    paySalonDebt: vi.fn(),
    setupSalonCard: vi.fn(),
    cancelSalonSubscription: vi.fn(),
    convertToSalon: vi.fn(),
  };
});
vi.mock("../../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/max-sdk")>();
  return { ...original, openPaymentConfirmation: vi.fn() };
});

import {
  cancelSalonSubscription,
  convertToSalon,
  getSalonBillingStatus,
  paySalonDebt,
  setupSalonCard,
  type MeResponse,
  type SalonSubscription,
} from "../../lib/admin-api";
import { ApiError } from "../../lib/api";
import { openPaymentConfirmation } from "../../lib/max-sdk";
import { SalonBillingScreen } from "./SalonBillingScreen";
import { SalonPlanChangeScreen } from "./SalonPlanChangeScreen";

const status = vi.mocked(getSalonBillingStatus);

const OWNER: MeResponse = {
  user: { id: "u-1", name: "Ирина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula" },
  role: "owner",
  capabilities: [],
  is_customer: false,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: null,
  landing_path: "/admin/team",
};
const ADMIN: MeResponse = { ...OWNER, role: "admin", is_owner: false, is_admin: true };

function sub(over: Partial<SalonSubscription> = {}): SalonSubscription {
  return {
    status: "active",
    tariff: "salon",
    trial_ends_at: null,
    current_period_end: "2026-11-01",
    frozen_at: null,
    frozen_reason: null,
    cancel_effective_at: null,
    pending_tariff: null,
    pending_tariff_from: null,
    card: null,
    masters_count: 4,
    extra_masters_amount: "150.00",
    price_preview: "1140.00",
    ...over,
  };
}

function renderBilling(me: MeResponse = OWNER) {
  render(
    <MemoryRouter initialEntries={["/admin/billing"]}>
      <Routes>
        <Route path="/admin/billing" element={<SalonBillingScreen me={me} />} />
        <Route path="/admin/billing/salon" element={<SalonPlanChangeScreen me={me} />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("состав по листу DRF-2988 п.4", () => {
  it("тариф, мастера, доплата сверх трёх и итог — из ответа каталога", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub() });
    renderBilling();

    expect(await screen.findByText("Активна")).toBeInTheDocument();
    expect(screen.getByText(/тариф «Салон»/)).toBeInTheDocument();
    expect(screen.getByText("Активных мастеров: 4.")).toBeInTheDocument();
    expect(screen.getByText(/Доплата за мастеров сверх трёх: 150 ₽ в месяц/)).toBeInTheDocument();
    expect(screen.getByText(/К оплате за период: 1 140 ₽/)).toBeInTheDocument();
  });

  it("пробный — до даты; ожидающий тариф — с даты", async () => {
    status.mockResolvedValue({
      tenant_id: "t-1",
      subscription: sub({
        status: "trial",
        tariff: "solo",
        trial_ends_at: "2026-11-20",
        pending_tariff: "salon",
        pending_tariff_from: "2026-11-21",
        extra_masters_amount: "0.00",
      }),
    });
    renderBilling();

    expect(await screen.findByText("Пробный период — до 20 ноября 2026.")).toBeInTheDocument();
    expect(screen.getByText(/С 21 ноября 2026 — тариф «\s*Салон»/)).toBeInTheDocument();
    // Доплаты нет — строки нет; тариф и пробный выше это подтверждают.
    expect(screen.queryByText(/Доплата за мастеров/)).toBeNull();
  });

  it("подписки ещё нет — говорит, когда она появится", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: null });
    renderBilling();

    expect(await screen.findByText(/Подписка откроется, когда салон появится в каталоге/))
      .toBeInTheDocument();
  });
});

describe("заморозка и долг", () => {
  it("при заморозке — оплата долга; сумма после списания", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub({ status: "frozen" }) });
    vi.mocked(paySalonDebt).mockResolvedValue({
      payment_id: "p",
      invoice_id: "i",
      confirmation_url: null,
      amount: "1140.00",
      status: "succeeded",
      subscription_status: "active",
    });
    renderBilling(ADMIN);

    expect(await screen.findByText(/Подписка приостановлена/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Оплатить долг" }));

    expect(await screen.findByText("Списано 1 140 ₽. Задолженность погашена.")).toBeInTheDocument();
    expect(vi.mocked(paySalonDebt)).toHaveBeenCalledWith(
      `${window.location.origin}/admin/billing`,
    );
  });

  it("платёж без подтверждения — не говорит, что долг погашен", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub({ status: "past_due" }) });
    vi.mocked(paySalonDebt).mockResolvedValue({
      payment_id: "p",
      invoice_id: "i",
      confirmation_url: null,
      amount: "1140.00",
      status: "pending",
      subscription_status: "past_due",
    });
    renderBilling();

    await userEvent.click(await screen.findByRole("button", { name: "Оплатить долг" }));

    expect(await screen.findByText(/Платёж принят в обработку/)).toBeInTheDocument();
    expect(screen.queryByText(/Задолженность погашена/)).toBeNull();
  });

  it("списания выключены — честно говорит, что оплата не подключена", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub({ status: "past_due" }) });
    vi.mocked(paySalonDebt).mockRejectedValue(
      new ApiError(503, "billing_charges_disabled", "charges are not enabled yet"),
    );
    renderBilling();

    await userEvent.click(await screen.findByRole("button", { name: "Оплатить долг" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Оплата пока не подключена — списаний сейчас нет.",
    );
  });
});

describe("карта", () => {
  it("привязка — только после согласия, и открывает форму провайдера", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub() });
    vi.mocked(setupSalonCard).mockResolvedValue({ confirmation_url: "https://pay.test/c" });
    renderBilling();

    const bind = await screen.findByRole("button", { name: "Привязать карту" });
    expect(bind).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(bind);

    await waitFor(() =>
      expect(vi.mocked(openPaymentConfirmation)).toHaveBeenCalledWith("https://pay.test/c"),
    );
  });

  it("привязанная карта — марка и последние цифры", async () => {
    status.mockResolvedValue({
      tenant_id: "t-1",
      subscription: sub({ card: { last4: "4242", brand: "mir" } }),
    });
    renderBilling();

    expect(await screen.findByText("Мир")).toBeInTheDocument();
    expect(screen.getByText("·· 4242")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Привязать карту" })).toBeNull();
  });
});

describe("кто что может", () => {
  it("отмена — у владельца, с подтверждением; у админа её нет", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub() });
    vi.mocked(cancelSalonSubscription).mockResolvedValue({
      status: "canceled",
      effective_at: "2026-11-02",
    });
    renderBilling();

    await userEvent.click(await screen.findByRole("button", { name: "Отменить подписку" }));
    await userEvent.click(screen.getByRole("button", { name: "Да, отменить" }));
    await waitFor(() => expect(vi.mocked(cancelSalonSubscription)).toHaveBeenCalledTimes(1));
  });

  it("у администратора — статус есть, отмены нет", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub() });
    renderBilling(ADMIN);

    expect(await screen.findByText("Активна")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить подписку" })).toBeNull();
  });

  it("ресепшн получает отказ сервера — экран называет, кто управляет", async () => {
    status.mockRejectedValue(new ApiError(403, "role_not_allowed", "x"));
    renderBilling({ ...OWNER, is_owner: false, is_receptionist: true });

    expect(
      await screen.findByText(
        "Подпиской управляют владелец и администратор салона — если салон приостановлен, " +
          "попросите их оплатить.",
      ),
    ).toBeInTheDocument();
  });
});

describe("переход «Соло» → «Салон» (В-2)", () => {
  it("с экрана подписки соло — к подтверждению с ценой; цену шлёт обратно", async () => {
    status.mockResolvedValue({
      tenant_id: "t-1",
      subscription: sub({ tariff: "solo", masters_count: 1, extra_masters_amount: "0.00" }),
    });
    vi.mocked(convertToSalon).mockResolvedValue({ tariff: "salon", effective_from: "2026-11-01" });
    renderBilling();

    await userEvent.click(await screen.findByRole("button", { name: "Перейти на «Салон»" }));
    await userEvent.click(await screen.findByRole("button", { name: "Перейти за 990 ₽" }));

    expect(await screen.findByText(/Готово: можно добавлять мастеров/)).toBeInTheDocument();
    expect(vi.mocked(convertToSalon).mock.calls[0]?.[0]).toBe("990.00");
  });

  it("цена изменилась — показывает каталожную и ждёт нового подтверждения", async () => {
    vi.mocked(convertToSalon).mockRejectedValueOnce(
      new ApiError(409, "price_mismatch", "x", { current_price: "1090.00" }),
    );
    render(
      <MemoryRouter initialEntries={["/admin/billing/salon"]}>
        <Routes>
          <Route path="/admin/billing/salon" element={<SalonPlanChangeScreen me={OWNER} />} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Перейти за 990 ₽" }));

    expect(await screen.findByRole("button", { name: "Перейти за 1 090 ₽" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Цена изменилась");

    // Новая цена — новое решение: второе подтверждение идёт с новым ключом.
    vi.mocked(convertToSalon).mockResolvedValueOnce({
      tariff: "salon",
      effective_from: "2026-11-01",
    });
    await userEvent.click(screen.getByRole("button", { name: "Перейти за 1 090 ₽" }));
    const [first, second] = vi.mocked(convertToSalon).mock.calls;
    expect(second?.[0]).toBe("1090.00");
    expect(second?.[1]).toBeTruthy();
    expect(second?.[1]).not.toBe(first?.[1]);
  });

  it("после открытия формы привязки — можно привязать заново", async () => {
    status.mockResolvedValue({ tenant_id: "t-1", subscription: sub() });
    vi.mocked(setupSalonCard).mockResolvedValue({ confirmation_url: "https://pay.test/c" });
    renderBilling();

    await userEvent.click(await screen.findByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Привязать карту" }));
    await userEvent.click(await screen.findByRole("button", { name: "Привязать заново" }));

    expect(screen.getByRole("button", { name: "Привязать карту" })).toBeInTheDocument();
  });
});
