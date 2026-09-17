/**
 * Экран 07 «Профиль мастера» — макет 6 (DRF-1814, часть B из трёх).
 *
 * Заперто:
 * C1 — превью 6.4 — ТОТ ЖЕ `MasterCard`, что видит клиент (по классу и имени из контракта);
 * C2 — бейдж «Принимает сегодня» рисуется ТОЛЬКО при `accepts_today === true`; при
 *      `false` с любой причиной его нет (пара на одних данных);
 * C3 — чипы = `categories` из контракта; строки-специализации в превью нет;
 * L1 — лимит «О себе» — `limits.bio` из ответа сервера: 300 символов проходят
 *      (старый литерал 280 не режет), 501 — отказ с числом 500;
 * L2 — литерала `MASTER_PROFILE_BIO_MAX` в `master-api` больше нет (сторож «в MA один
 *      лимит, из контракта»);
 * N1 — имя с «Изменить»: короче `limits.display_name_min` — отказ без запроса;
 *      сохранение шлёт `display_name` и берёт ответ сервера;
 * W1 — портфолио: счётчик «N из limit» — из контракта; при `count === limit` кнопки
 *      «Добавить» нет (пара: при 2/10 — есть);
 * W2 — «Пропустить пока» на пустом портфолио сворачивает блок и ничего не шлёт;
 * W3 — удаление работы зовёт API и перерисовывает список из ответа;
 * P1 — выбор фото открывает кроп-редактор (зум, поворот), «Сохранить» шлёт квадрат;
 * H1 — подсказки 6.1 («настоящее фото», «без фильтров») на экране;
 * E1 — карточка не загрузилась — ошибка с повтором, не пустой экран.
 *
 * Сторож от зависимости от времени — как в тестах экранов 03/04.
 */
import { act, configure, fireEvent, getConfig, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getMasterMe: vi.fn(),
    getMasterProfileCard: vi.fn(),
    patchMasterProfile: vi.fn(),
    uploadMasterProfilePhoto: vi.fn(),
    getPortfolio: vi.fn(),
    uploadPortfolioPhoto: vi.fn(),
    deletePortfolioItem: vi.fn(),
  };
});
vi.mock("../lib/image-crop", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/image-crop")>();
  return {
    ...original,
    // jsdom не рисует canvas: результат кропа подменяется, а вход (зум/поворот)
    // проверяется по аргументам.
    renderSquareCrop: vi.fn(),
    loadImage: vi.fn(),
  };
});

import * as api from "../lib/master-api";
import {
  deletePortfolioItem,
  getMasterMe,
  getMasterProfileCard,
  getPortfolio,
  patchMasterProfile,
  uploadMasterProfilePhoto,
  uploadPortfolioPhoto,
  type MasterMeResponse,
  type MasterProfileCard,
  type PortfolioList,
} from "../lib/master-api";
import { loadImage, renderSquareCrop } from "../lib/image-crop";
import { MasterProfileScreen, PROFILE_COPY } from "./MasterProfileScreen";

const GUARD_ASYNC_TIMEOUT_MS = 20;
let previousAsyncUtilTimeout = 1000;
beforeAll(() => {
  previousAsyncUtilTimeout = getConfig().asyncUtilTimeout;
  configure({ asyncUtilTimeout: GUARD_ASYNC_TIMEOUT_MS });
});
afterAll(() => {
  configure({ asyncUtilTimeout: previousAsyncUtilTimeout });
});

const settle = async (rounds = 4) => {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {});
  }
};

const LIMITS = {
  bio: 500,
  display_name_min: 2,
  avatar_bytes: 5 * 1024 * 1024,
  portfolio_bytes: 10 * 1024 * 1024,
  portfolio_count: 10,
};

const me = (): MasterMeResponse => ({
  master: {
    id: "m-1",
    name: "Анна из /me",
    specialization: "",
    bio: "",
    photo_url: "",
    services: [{ id: "s-1", name: "Маникюр", duration_min: 60 }],
  },
  salon: { tenant_id: "t-1", name: "Салон" },
  permissions: { can_edit_schedule: true, can_edit_services: false, can_message_customers: false },
});

const card = (over: Partial<MasterProfileCard> = {}): MasterProfileCard => ({
  master: { id: "m-1", name: "Анна Петрова", bio: "Опыт 5 лет", photo_url: "" },
  limits: { ...LIMITS },
  portfolio: { count: 2, limit: 10 },
  accepts_today: true,
  accepts_today_reason: null,
  categories: ["Маникюр", "Педикюр"],
  categories_reason: null,
  ...over,
});

const portfolio = (n: number): PortfolioList => ({
  items: Array.from({ length: n }, (_, i) => ({
    id: `w-${i + 1}`,
    image_url: `https://catalog.test/w${i + 1}.jpg`,
    sort_order: i,
    created_at: "2026-09-17T12:00:00+00:00",
  })),
  count: n,
  limit: 10,
});

function mountScreen() {
  return render(
    <MemoryRouter initialEntries={["/solo/profile"]}>
      <MasterProfileScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(getMasterMe).mockReset().mockResolvedValue(me());
  vi.mocked(getMasterProfileCard).mockReset().mockResolvedValue(card());
  vi.mocked(getPortfolio).mockReset().mockResolvedValue(portfolio(2));
  vi.mocked(patchMasterProfile).mockReset();
  vi.mocked(uploadMasterProfilePhoto).mockReset();
  vi.mocked(uploadPortfolioPhoto).mockReset();
  vi.mocked(deletePortfolioItem).mockReset();
  vi.mocked(renderSquareCrop).mockReset();
  vi.mocked(loadImage).mockReset();
});

describe("6.4 превью — тот же MasterCard, бейдж только из слота, чипы из шаблонов", () => {
  it("C1: превью рендерит клиентскую карточку с именем из контракта", async () => {
    const { container } = mountScreen();
    await settle();

    const preview = container.querySelector(".master-card");
    expect(preview).not.toBeNull();
    expect(within(preview as HTMLElement).getByText("Анна Петрова")).toBeInTheDocument();
  });

  it("C2: accepts_today=true → бейдж есть; false с причиной → бейджа нет", async () => {
    const first = mountScreen();
    await settle();
    expect(screen.getByText(PROFILE_COPY.preview.acceptsToday)).toBeInTheDocument();
    first.unmount();

    vi.mocked(getMasterProfileCard).mockResolvedValue(
      card({ accepts_today: false, accepts_today_reason: "no_slots" }),
    );
    mountScreen();
    await settle();
    expect(screen.queryByText(PROFILE_COPY.preview.acceptsToday)).toBeNull();
  });

  it("C3: чипы — категории из контракта, строки-специализации нет", async () => {
    const { container } = mountScreen();
    await settle();

    const chips = container.querySelectorAll(".master-card__chip");
    expect(Array.from(chips).map((c) => c.textContent)).toEqual(["Маникюр", "Педикюр"]);
    expect(container.querySelector(".master-card__spec")).toBeNull();
  });
});

describe("6.1 — лимиты из контракта, имя с «Изменить», подсказки", () => {
  it("L1: 300 символов «О себе» проходят, 501 — отказ с числом из limits.bio", async () => {
    vi.mocked(patchMasterProfile).mockResolvedValue({
      master: { id: "m-1", name: "Анна Петрова", bio: "б".repeat(300), photo_url: "" },
    });
    mountScreen();
    await settle();

    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.editBio));
    const textarea = screen.getByRole("textbox", { name: PROFILE_COPY.bioEdit.title });
    fireEvent.change(textarea, { target: { value: "б".repeat(300) } });
    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.save));
    await settle();
    expect(patchMasterProfile).toHaveBeenCalledWith({ bio: "б".repeat(300) });

    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.editBio));
    fireEvent.change(screen.getByRole("textbox", { name: PROFILE_COPY.bioEdit.title }), {
      target: { value: "б".repeat(501) },
    });
    expect(screen.getByText("501 / 500")).toBeInTheDocument();
    expect(patchMasterProfile).toHaveBeenCalledTimes(1);
  });

  it("L2: литерала MASTER_PROFILE_BIO_MAX в master-api больше нет", () => {
    expect(Object.keys(api)).not.toContain("MASTER_PROFILE_BIO_MAX");
  });

  it("N1: имя короче display_name_min — отказ без запроса; длиннее — PATCH display_name", async () => {
    vi.mocked(patchMasterProfile).mockResolvedValue({
      master: { id: "m-1", name: "Аня", bio: "Опыт 5 лет", photo_url: "" },
    });
    mountScreen();
    await settle();

    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.editName));
    const input = screen.getByRole("textbox", { name: PROFILE_COPY.nameEdit.title });
    fireEvent.change(input, { target: { value: "А" } });
    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.save));
    await settle();
    expect(patchMasterProfile).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("2");

    fireEvent.change(input, { target: { value: "Аня" } });
    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.save));
    await settle();
    expect(patchMasterProfile).toHaveBeenCalledWith({ display_name: "Аня" });
    expect(screen.getAllByText("Аня").length).toBeGreaterThan(0);
  });

  it("H1: подсказки про настоящее фото и без фильтров — на экране", async () => {
    mountScreen();
    await settle();
    for (const hint of PROFILE_COPY.photoHints) {
      expect(screen.getByText(hint)).toBeInTheDocument();
    }
  });
});

describe("6.3 — работы: счётчик и лимит из контракта, «Пропустить пока», удаление", () => {
  it("W1: «2 из 10» из контракта и кнопка «Добавить» есть; при 10 из 10 — кнопки нет", async () => {
    const first = mountScreen();
    await settle();
    expect(screen.getByText(PROFILE_COPY.portfolio.counter(2, 10))).toBeInTheDocument();
    expect(screen.getByText(PROFILE_COPY.buttons.addWork)).toBeInTheDocument();
    first.unmount();

    vi.mocked(getMasterProfileCard).mockResolvedValue(card({ portfolio: { count: 10, limit: 10 } }));
    vi.mocked(getPortfolio).mockResolvedValue(portfolio(10));
    mountScreen();
    await settle();
    expect(screen.getByText(PROFILE_COPY.portfolio.counter(10, 10))).toBeInTheDocument();
    expect(screen.queryByText(PROFILE_COPY.buttons.addWork)).toBeNull();
  });

  it("W2: «Пропустить пока» на пустом портфолио сворачивает блок, запросов нет", async () => {
    vi.mocked(getMasterProfileCard).mockResolvedValue(card({ portfolio: { count: 0, limit: 10 } }));
    vi.mocked(getPortfolio).mockResolvedValue(portfolio(0));
    mountScreen();
    await settle();

    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.skipPortfolio));
    expect(screen.queryByText(PROFILE_COPY.buttons.addWork)).toBeNull();
    expect(screen.getByText(PROFILE_COPY.portfolio.skipped)).toBeInTheDocument();
    expect(uploadPortfolioPhoto).not.toHaveBeenCalled();
    expect(deletePortfolioItem).not.toHaveBeenCalled();
  });

  it("W3: удаление зовёт API с id и перерисовывает список из ответа", async () => {
    vi.mocked(deletePortfolioItem).mockResolvedValue({ count: 1, limit: 10 });
    vi.mocked(getPortfolio).mockResolvedValueOnce(portfolio(2)).mockResolvedValueOnce(portfolio(1));
    mountScreen();
    await settle();

    const buttons = screen.getAllByLabelText(PROFILE_COPY.portfolio.removeAria);
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0] as HTMLElement);
    await settle();

    expect(deletePortfolioItem).toHaveBeenCalledWith("w-1");
    expect(screen.getAllByLabelText(PROFILE_COPY.portfolio.removeAria)).toHaveLength(1);
    expect(screen.getByText(PROFILE_COPY.portfolio.counter(1, 10))).toBeInTheDocument();
  });
});

describe("6.2 — кроп 1:1", () => {
  it("P1: выбор фото открывает редактор; зум и поворот доезжают до кропа; «Сохранить» шлёт квадрат", async () => {
    const fakeImage = { width: 1200, height: 800 } as unknown as HTMLImageElement;
    vi.mocked(loadImage).mockResolvedValue(fakeImage);
    const square = new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" });
    vi.mocked(renderSquareCrop).mockResolvedValue(square);
    vi.mocked(uploadMasterProfilePhoto).mockResolvedValue({
      master: { id: "m-1", name: "Анна Петрова", bio: "Опыт 5 лет", photo_url: "https://c/a.jpg" },
    });
    const { container } = mountScreen();
    await settle();

    const input = container.querySelector('input[type="file"][data-role="avatar"]') as HTMLInputElement;
    const file = new File([new Uint8Array([9, 9])], "me.jpg", { type: "image/jpeg" });
    fireEvent.change(input, { target: { files: [file] } });
    await settle();

    expect(screen.getByRole("dialog", { name: PROFILE_COPY.crop.title })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(PROFILE_COPY.crop.zoom), { target: { value: "2" } });
    fireEvent.click(screen.getByText(PROFILE_COPY.crop.rotate));
    fireEvent.click(screen.getByText(PROFILE_COPY.crop.apply));
    await settle();

    expect(renderSquareCrop).toHaveBeenCalledTimes(1);
    const cropCall = vi.mocked(renderSquareCrop).mock.calls[0];
    expect(cropCall?.[1].zoom).toBe(2);
    expect(cropCall?.[1].rotation).toBe(90);
    const sent = vi.mocked(uploadMasterProfilePhoto).mock.calls[0]?.[0];
    expect(sent).toBeInstanceOf(File);
    expect(sent?.type).toBe("image/jpeg");
    expect(screen.queryByRole("dialog", { name: PROFILE_COPY.crop.title })).toBeNull();
  });
});

describe("ошибки", () => {
  it("E1: карточка не загрузилась — ошибка с повтором, повтор зовёт API снова", async () => {
    vi.mocked(getMasterProfileCard).mockRejectedValueOnce(new Error("down")).mockResolvedValue(card());
    mountScreen();
    await settle();

    expect(screen.getByText(PROFILE_COPY.states.errorTitle)).toBeInTheDocument();
    fireEvent.click(screen.getByText(PROFILE_COPY.buttons.retry));
    await settle();
    expect(getMasterProfileCard).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("Анна Петрова").length).toBeGreaterThan(0);
  });
});
