/**
 * Ноль и «не удалось узнать» — разные значения (DRF-2366).
 *
 * Карта кабинета (DRF-2365) нашла три места, где счётчик хранился числом с
 * умолчанием `0`, а неудачный запрос молча оставлял его нулём. Человек читал
 * ноль как факт. Цена названа в листе: мастер отправил запрос на изменение
 * графика, администратор видит ноль запросов и **не знает, что его ждут**.
 *
 * Отдельно измерено и подтверждено: запрос мастера **доезжает**. Обе стороны
 * работают с одной моделью `ScheduleChangeRequest`, мастер создаёт строку со
 * статусом «ожидает» и тенантом салона, кабинет её же и перечисляет. То есть
 * это дефект показа, а не потери — иначе чинить надо было бы не здесь.
 *
 * Хуже отсутствующего значка оказалась строка рядом с ним: при неудаче
 * карточка **утверждала** «Все запросы рассмотрены» и «Новых обсуждений
 * нет» — ровно то, чего она не знает.
 *
 * Узлы идут парами: ноль показывается как ноль, неизвестность — не как ноль.
 * Второй в каждой паре обязателен: без него правка превратила бы «ноль» в
 * «не знаю» и потеряла честный ноль.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getAvailabilityRequests: vi.fn(),
    listMasters: vi.fn(),
    getSalonReadiness: vi.fn(),
  };
});

// «Чаты с мастерами» живут в своём модуле, а не в общем admin-api.
vi.mock("../../lib/internal-chat-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../../lib/internal-chat-api")>();
  return { ...original, listAdminThreads: vi.fn() };
});

import { getAvailabilityRequests, listMasters } from "../../lib/admin-api";
import { listAdminThreads } from "../../lib/internal-chat-api";
import { countLabel, UNKNOWN_MARK } from "../../lib/format";
import { AdminAvailabilityRequestsScreen } from "./AdminAvailabilityRequestsScreen";
import { AdminTeamScreen } from "./AdminTeamScreen";

const mockedRequests = vi.mocked(getAvailabilityRequests);
const mockedThreads = vi.mocked(listAdminThreads);
const mockedMasters = vi.mocked(listMasters);

const OWNER = {
  is_owner: true,
  is_admin: true,
  is_master: false,
  is_client: false,
} as never;

function renderTeam() {
  render(
    <MemoryRouter initialEntries={["/admin/team"]}>
      <AdminTeamScreen me={OWNER} />
    </MemoryRouter>,
  );
}

describe("countLabel — знак «значения нет» вместо нуля", () => {
  it("число остаётся числом", () => {
    expect(countLabel(0)).toBe("0");
    expect(countLabel(7)).toBe("7");
  });

  it("неизвестность — знак, а не ноль", () => {
    expect(countLabel(null)).toBe(UNKNOWN_MARK);
    expect(countLabel(null)).not.toBe("0");
  });
});

describe("Значок «Запросы графика» на экране «Команда»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedMasters.mockResolvedValue({ items: [] } as never);
    mockedThreads.mockResolvedValue({ items: [] } as never);
  });

  it("ноль запросов называется нулём, а не неизвестностью", async () => {
    mockedRequests.mockResolvedValue({ items: [] } as never);
    renderTeam();

    expect(await screen.findByText("Все запросы рассмотрены")).toBeInTheDocument();
  });

  it("отказ не выдаётся за «все рассмотрены»", async () => {
    mockedRequests.mockRejectedValue(new Error("сеть недоступна"));
    renderTeam();

    await waitFor(() => {
      expect(screen.queryByText("Все запросы рассмотрены")).not.toBeInTheDocument();
    });
  });

  it("при отказе значок несёт знак «значения нет»", async () => {
    mockedRequests.mockRejectedValue(new Error("сеть недоступна"));
    renderTeam();

    expect(
      await screen.findByLabelText(`ожидают: ${UNKNOWN_MARK}`),
    ).toBeInTheDocument();
  });
});

describe("Значок «Чаты с мастерами»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedMasters.mockResolvedValue({ items: [] } as never);
    mockedRequests.mockResolvedValue({ items: [] } as never);
  });

  it("ноль обсуждений называется нулём", async () => {
    mockedThreads.mockResolvedValue({ items: [] } as never);
    renderTeam();

    expect(await screen.findByText("Новых обсуждений нет")).toBeInTheDocument();
  });

  it("отказ не выдаётся за «новых нет»", async () => {
    mockedThreads.mockRejectedValue(new Error("сеть недоступна"));
    renderTeam();

    await waitFor(() => {
      expect(screen.queryByText("Новых обсуждений нет")).not.toBeInTheDocument();
    });
  });
});


describe("Чипы разделов на экране «Запросы графика»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedMasters.mockResolvedValue({ items: [] } as never);
    mockedThreads.mockResolvedValue({ items: [] } as never);
  });

  function renderRequests() {
    render(
      <MemoryRouter initialEntries={["/admin/availability-requests"]}>
        <AdminAvailabilityRequestsScreen me={OWNER} />
      </MemoryRouter>,
    );
  }

  it("ноль называется нулём", async () => {
    mockedRequests.mockResolvedValue({ items: [] } as never);
    renderRequests();

    expect(await screen.findByText("● Ожидают (0)")).toBeInTheDocument();
  });

  it("отказ не выдаётся за ноль", async () => {
    mockedRequests.mockRejectedValue(new Error("сеть недоступна"));
    renderRequests();

    expect(
      await screen.findByText(`● Ожидают (${UNKNOWN_MARK})`),
    ).toBeInTheDocument();
    expect(screen.queryByText("● Ожидают (0)")).not.toBeInTheDocument();
  });
});
