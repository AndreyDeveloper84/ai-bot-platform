/**
 * Вход в профиль по аватарке (решение владельца §77 п.60).
 *
 * Сторожа держат три условия, и все три — про то, что дверь **единственная**:
 *   - она есть на КАЖДОМ экране нижней панели, кроме самого профиля (туда
 *     дверь вела бы на себя же — в доме правило «активная вкладка не
 *     кликается», `CustomerTabBar`);
 *   - без имени она всё равно нажимается и показывает «·», а не пустоту:
 *     заглушка-украшение спрятала бы единственный вход;
 *   - цель нажатия 44×44 (WCAG 2.5.8), при кружке 36 px.
 *
 * Цель нажатия 44 стережёт отдельный файл
 * (`customerAvatarEntry.build.test.ts`): она проверяется чтением CSS с
 * диска, а `node:fs` в этом пакете не типизирован — `tsconfig` исключает
 * из проверки типов именно `*.build.test.ts`.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CustomerAvatarEntry, primeDisplayName } from "./CustomerAvatarEntry";

/**
 * Ручка подменяется по-настоящему: узел об отказе обязан исполнять
 * производственный `.catch`, а не свой собственный. Иначе он проверяет тест.
 */
vi.mock("../lib/customer-profile", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/customer-profile")>();
  return { ...real, fetchMe: vi.fn() };
});

const { fetchMe } = await import("../lib/customer-profile");
const fetchMeMock = fetchMe as unknown as ReturnType<typeof vi.fn>;

const SOURCES = import.meta.glob(["../screens/**/*.tsx", "../components/**/*.tsx", "../App.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;


const isTest = (path: string) => path.includes(".test.");
const baseName = (path: string) => path.split("/").pop() as string;

/**
 * Профиль — единственный экран панели без двери, и он назван ЗДЕСЬ, а не
 * «все, кроме тех, где её нет»: отрицательный признак пропустил бы экран,
 * с которого дверь однажды снимут молча.
 */
const WITHOUT_ENTRY = ["CustomerProfileScreen.tsx"];

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

function renderEntry(props: { displayName?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={["/customer/records"]}>
      <Routes>
        <Route path="/customer/records" element={<CustomerAvatarEntry {...props} />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Мемо имени — модульное: без сброса второй тест получил бы имя первого.
  primeDisplayName(null);
  fetchMeMock.mockReset();
});

describe("дверь в профиль — на каждом экране панели", () => {
  it("каждый экран с панелью несёт дверь, кроме названного профиля", () => {
    const withBar: string[] = [];
    const missing: string[] = [];
    for (const [path, source] of Object.entries(SOURCES)) {
      if (isTest(path)) continue;
      if (baseName(path) === "CustomerAvatarEntry.tsx") continue;
      // Подстрокой, а не регуляркой по тегу: регулярка, дочитывающая до
      // «>», обрывается внутри `{() => …}` и недосчитывает молча.
      if (!source.includes("<CustomerTabBar")) continue;
      withBar.push(baseName(path));
      if (WITHOUT_ENTRY.includes(baseName(path))) continue;
      if (!source.includes("<CustomerAvatarEntry")) missing.push(baseName(path));
    }

    // Наличие прежде отсутствия: пустой охват сделал бы «ни одного
    // пропуска» правдой о пустоте.
    expect(withBar.length).toBeGreaterThanOrEqual(6);
    expect(withBar).toContain("CustomerProfileScreen.tsx");
    expect(missing).toEqual([]);
  });

  it("реализация одна — второй копии кружка-двери в исходниках нет", () => {
    const owners = Object.entries(SOURCES)
      .filter(([path]) => !isTest(path))
      .filter(([, source]) => source.includes('className="customer-avatar-entry"'))
      .map(([path]) => baseName(path));
    expect(owners).toEqual(["CustomerAvatarEntry.tsx"]);
  });
});

describe("дверь ведёт в профиль и работает без имени", () => {
  it("нажатие уводит на /customer/profile", () => {
    renderEntry({ displayName: "Анна Петрова" });
    fireEvent.click(screen.getByRole("button", { name: "Мой профиль" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/profile");
  });

  it("инициалы — из имени", () => {
    renderEntry({ displayName: "Анна Петрова" });
    expect(screen.getByText("АП")).toBeInTheDocument();
  });

  it("без имени кружок показывает «·» и остаётся нажимаемым", () => {
    renderEntry({ displayName: "" });
    expect(screen.getByText("·")).toBeInTheDocument();
    const door = screen.getByRole("button", { name: "Мой профиль" });
    expect(door).toBeEnabled();
    fireEvent.click(door);
    expect(screen.getByTestId("location")).toHaveTextContent("/customer/profile");
  });

  it("имя приходит из общего источника, когда экран его не передал", async () => {
    fetchMeMock.mockResolvedValueOnce({ display_name: "Мария Иванова" });
    renderEntry();
    expect(await screen.findByText("МИ")).toBeInTheDocument();
    expect(fetchMeMock).toHaveBeenCalledTimes(1);
  });

  it("экран, у которого имя есть, отдаёт его в общий источник", async () => {
    // Иначе первый переход Главная → Дневник рисует «·» на целый круг к
    // `/me`, хотя имя было в руках экраном раньше.
    render(
      <MemoryRouter>
        <CustomerAvatarEntry displayName="Анна Петрова" />
        <CustomerAvatarEntry />
      </MemoryRouter>,
    );
    // `findAllByText` отдаёт результат по ПЕРВОМУ совпадению и второго не
    // ждёт — ждать надо длину, иначе узел зеленел бы на одном кружке.
    await waitFor(() => expect(screen.getAllByText("АП")).toHaveLength(2));
    expect(fetchMeMock).not.toHaveBeenCalled();
  });

  it("отказ ручки не снимает дверь", async () => {
    // Отказ обрабатывает КОД, а не тест. Прежняя редакция этого узла
    // подсовывала уже обработанное обещание, поэтому производственный
    // `.catch` не исполнялся вовсе: сними его — и ничего не краснело.
    fetchMeMock.mockRejectedValueOnce(new Error("нет сети"));
    renderEntry();
    expect(await screen.findByText("·")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мой профиль" })).toBeEnabled();
  });

  it("отказ не запоминается — следующий монтаж спрашивает снова", async () => {
    // Иначе один офлайн прибивал «·» до перезапуска приложения, уже после
    // того как сеть вернулась.
    fetchMeMock.mockRejectedValueOnce(new Error("нет сети"));
    const first = renderEntry();
    expect(await screen.findByText("·")).toBeInTheDocument();
    first.unmount();

    fetchMeMock.mockResolvedValueOnce({ display_name: "Мария Иванова" });
    renderEntry();
    expect(await screen.findByText("МИ")).toBeInTheDocument();
    expect(fetchMeMock).toHaveBeenCalledTimes(2);
  });
});
