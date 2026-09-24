/**
 * Кто владеет адресом превью — и когда он освобождается (DRF-2394, DRF-2399).
 *
 * Два листа, две половины одного вопроса. Узлы намеренно разделены, чтобы
 * откат был по частям.
 *
 * ### DRF-2399 — экран результата
 *
 * Сегодня адрес создаёт экран обработки, **передаёт его навигацией** экрану
 * результата, и он же освобождает его при своём уходе. Владелец по
 * построению не назван: создал один, рисует другой, освобождает первый —
 * уже уйдя.
 *
 * **Замер до правки, настоящий Chrome** (создать адрес → присвоить `src` →
 * отозвать):
 *
 * ```
 * отзыв синхронно сразу после src :  СЛОМАНО
 * отзыв в микротаске              :  ЗАГРУЗИЛОСЬ
 * отзыв через setTimeout(0)       :  ЗАГРУЗИЛОСЬ
 * отзыв до присвоения src         :  СЛОМАНО
 * ```
 *
 * Очистка `useEffect` — пассивный эффект: она бежит ПОСЛЕ фазы мутации
 * DOM, когда `src` уже присвоен и браузер забрал данные. Значит **на
 * первом показе человек картинку видит**, и формулировка «мертва к моменту
 * отрисовки» неверна. Дефект в другом: всё держится на совпадении
 * порядка. Любое **повторное** обращение к тому же адресу — новый узел,
 * возврат по истории, пересоздание — рисует по мёртвому адресу.
 *
 * Поэтому главный узел здесь — **повторный показ**: первый показ дефекта
 * не ловит вовсе.
 *
 * Решение: **владеет тот, кто рисует.** Экран результата получает `photo`
 * (он его уже получает) и создаёт свой адрес сам; экран обработки
 * освобождает только свой. Передавать адрес между экранами не нужно — это
 * не оптимизация, а устранение самой возможности пережить владельца.
 *
 * ### DRF-2394 — экран онбординга мастера
 *
 * Две отдельные правки, и в теле PR они названы двумя строками.
 *
 * **(а)** Очистка при уходе объявлена с пустыми зависимостями и читает
 * превью из первого рендера, где его ещё нет, — последний адрес не
 * освобождается. Поправка листа: освобождение СОХРАНЁННОГО адреса при
 * новом выборе есть и работает. Но и моя первая формулировка («один адрес
 * на посещение экрана») неточна: под `StrictMode` каждый выбор оставлял
 * ещё и сироту, см. (б).
 *
 * **(б)** Освобождение стоит ВНУТРИ обновления состояния
 * (`setDraft((prev) => { revoke(prev.…) })`) — побочный эффект в функции,
 * обязанной быть чистой. Приложение обёрнуто в `StrictMode`, который такие
 * функции вызывает дважды: каждый выбор создаёт два адреса, а сохраняет
 * один. Без выноса наружу правка (а) этой половины не лечит.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/food-scanner", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/food-scanner")>();
  return { ...original, logMeal: vi.fn(), scanPhoto: vi.fn() };
});
vi.mock("../lib/customer-wellness", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-wellness")>();
  return { ...original, getWellnessToday: vi.fn() };
});
vi.mock("../hooks/useScreenBack", () => ({ useScreenBack: () => vi.fn() }));
vi.mock("../lib/master-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/master-api")>();
  return { ...original, claimInvite: vi.fn(), acceptInvite: vi.fn() };
});
vi.mock("../lib/max-sdk", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/max-sdk")>();
  return { ...original, setBackButton: vi.fn(), signalReady: vi.fn() };
});

import { getWellnessToday } from "../lib/customer-wellness";
import { claimInvite } from "../lib/master-api";
import { MasterOnboardingScreen } from "./MasterOnboardingScreen";
import { scanPhoto, type ScanResponse } from "../lib/food-scanner";
import { FoodScannerProcessingScreen } from "./FoodScannerProcessingScreen";
import { FoodScannerResultScreen } from "./FoodScannerResultScreen";

/** Ответ приглашения по форме контракта — экран доходит до шага с фото. */
const CLAIM = {
  master: {
    id: "m-1",
    name: "Мария Петрова",
    specialization: "Маникюр",
    bio: "",
    photo_url: "",
    services: [{ id: "s1", name: "Маникюр", duration_min: 60 }],
    working_hours_summary: "пн–пт 10:00–19:00",
  },
  salon: { tenant_id: "t-1", name: "Ayla Beauty" },
  max_user: { first_name: "Мария", phone_masked: "+7 ··· ·· 21", max_handle: "@maria" },
};

const RESULT: ScanResponse = {
  scan_id: "scan-2399",
  dish_name: "Борщ",
  confidence: 0.83,
  portion_g: 300,
  nutrition: { calories: 250, protein_g: 12, fat_g: 8, carbs_g: 32 },
  beauty_insights: null,
};

/** Исходники экранов — для узла о контракте перехода. */
const SCREEN_SOURCES = import.meta.glob("./FoodScanner*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Счётчик адресов: что создано, что отозвано и что ещё живо. */
function trackObjectUrls() {
  let n = 0;
  const created: string[] = [];
  const revoked: string[] = [];
  // Наследник, а не россыпь свойств: разложить `URL` в объект значило бы
  // отнять конструктор, и любой `new URL(...)` в дереве падал бы с видом
  // постороннего сбоя (найдено ревью). `vi.unstubAllGlobals` возвращает
  // всё на место, потому что настоящий `URL` не тронут.
  class TestURL extends URL {}
  Object.defineProperty(TestURL, "createObjectURL", {
    value: (obj: unknown) => {
      void obj;
      const url = `blob:test/${++n}`;
      created.push(url);
      return url;
    },
    writable: true,
  });
  Object.defineProperty(TestURL, "revokeObjectURL", {
    value: (url: string) => {
      revoked.push(url);
    },
    writable: true,
  });
  vi.stubGlobal("URL", TestURL);
  return {
    created,
    revoked,
    alive: () => created.filter((u) => !revoked.includes(u)),
  };
}

function renderResult(photo: File) {
  return render(
    <StrictMode>
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/customer/food-scanner/result",
          // `previewUrl` НЕ передаётся: владеет тот, кто рисует.
          state: { result: RESULT, photo, mealType: "lunch" },
        },
      ]}
    >
      <Routes>
        <Route
          path="/customer/food-scanner/result"
          element={<FoodScannerResultScreen />}
        />
        <Route path="*" element={<div>другой экран</div>} />
      </Routes>
    </MemoryRouter>
    </StrictMode>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getWellnessToday).mockResolvedValue({
    display_name: "",
    nutrition_numbers_hidden: false,
  } as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DRF-2399 — адресом владеет тот, кто рисует", () => {
  it("первый показ: картинка рисуется по ЖИВОМУ адресу", async () => {
    const urls = trackObjectUrls();
    const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });
    renderResult(photo);

    const img = await screen.findByAltText("Фото блюда");
    const src = img.getAttribute("src") ?? "";
    // Присутствие первым: картинка вообще есть и адрес у неё наш.
    expect(src).toMatch(/^blob:test\//);
    expect(urls.revoked).not.toContain(src);
  });

  // Узел соответствия, а НЕ регрессии: на старом коде он падал бы оттого,
  // что картинки нет вовсе (адрес приходил навигацией), а не оттого, что
  // адрес мёртв. Настоящую регрессию ловит узел передачи ниже — так и
  // названо, чтобы имя не обещало большего (найдено ревью).
  it("повторный показ: адрес снова живой", async () => {
    const urls = trackObjectUrls();
    const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });

    const first = renderResult(photo);
    await screen.findByAltText("Фото блюда");
    first.unmount();

    // Тот же файл, второй заход — как возврат по истории или пересоздание
    // узла. Сегодня экран получал бы адрес, отозванный чужим экраном.
    renderResult(photo);
    const img = await screen.findByAltText("Фото блюда");
    const src = img.getAttribute("src") ?? "";
    expect(src).toMatch(/^blob:test\//);
    expect(urls.revoked).not.toContain(src);
  });

  it("уход с экрана освобождает СВОЙ адрес — и не оставляет чужих", async () => {
    const urls = trackObjectUrls();
    const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });

    const view = renderResult(photo);
    await screen.findByAltText("Фото блюда");
    // Присутствие первым: адрес создан, иначе «всё освобождено» было бы
    // правдой на пустом месте.
    expect(urls.created.length).toBeGreaterThan(0);

    view.unmount();
    await waitFor(() => expect(urls.alive()).toEqual([]));
  });
});

describe("DRF-2399 — передача между экранами: настоящая регрессия", () => {
  /**
   * Дефект жил в ПЕРЕХОДЕ, значит узел обязан его пересечь.
   *
   * Экран обработки создаёт адрес, уходит — и раньше отзывал адрес,
   * которым уже рисовал экран результата. Узлы выше этого не ловили: на
   * старом коде они падали оттого, что картинки нет вовсе, а не оттого,
   * что адрес мёртв (найдено ревью).
   */
  it("картинка на результате живёт после ухода экрана обработки", async () => {
    const urls = trackObjectUrls();
    const photo = new File(["x"], "meal.jpg", { type: "image/jpeg" });
    vi.mocked(scanPhoto).mockResolvedValue(RESULT);

    render(
      <StrictMode>
        <MemoryRouter
          initialEntries={[
            {
              pathname: "/customer/food-scanner/processing",
              state: { photo, mealType: "lunch" },
            },
          ]}
        >
          <Routes>
            <Route
              path="/customer/food-scanner/processing"
              element={<FoodScannerProcessingScreen />}
            />
            <Route
              path="/customer/food-scanner/result"
              element={<FoodScannerResultScreen />}
            />
            <Route path="*" element={<div>другой экран</div>} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );

    // Присутствие первым: ПЕРЕХОД СОСТОЯЛСЯ — ждём заголовок результата,
    // а не картинку. Подпись «Фото блюда» одинакова у обоих экранов, и
    // первая редакция узла брала картинку ещё не ушедшего экрана
    // обработки, отчего краснела на верном коде (поймано прогоном).
    await screen.findByRole("heading", { level: 1, name: "Я распознала так" });
    const img = await screen.findByAltText("Фото блюда");
    const src = img.getAttribute("src") ?? "";
    expect(src).toMatch(/^blob:test\//);

    // ДОЖДАТЬСЯ очистки ушедшего экрана, прежде чем утверждать. Первая
    // редакция узла этого не делала и проходила даже на подложенной
    // передаче адреса: пассивные эффекты ушедшего экрана ещё не успели
    // сработать к моменту утверждения (поймано мутацией).
    await waitFor(() => expect(urls.revoked.length).toBeGreaterThan(0));

    // И картинка рисуется по ЖИВОМУ адресу, хотя экран обработки уже ушёл.
    expect(urls.revoked).not.toContain(src);
  });

  it("экран обработки не передаёт адрес наружу — контракт перехода", async () => {
    const source = SCREEN_SOURCES["./FoodScannerProcessingScreen.tsx"];
    // Присутствие первым: файл прочитан и это он.
    expect(source).toContain("food-scanner/result");
    // Передача адреса навигацией — то, что убрано; строка не должна
    // вернуться незамеченной.
    expect(source).not.toMatch(/state:\s*\{[^}]*previewUrl/);
  });
});

describe("DRF-2394 — превью мастера освобождается у владельца", () => {
  /** Довести экран до шага 3: подтвердить личность → «Понятно» → фото. */
  async function atPhotoStep() {
    vi.mocked(claimInvite).mockResolvedValue(CLAIM as never);
    const view = render(
      <StrictMode>
        <MemoryRouter initialEntries={["/onboarding/master?token=t-1"]}>
          <Routes>
            <Route path="/onboarding/master" element={<MasterOnboardingScreen />} />
            <Route path="*" element={<div>другой экран</div>} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Это я, продолжить" }));
    fireEvent.click(await screen.findByRole("button", { name: "Понятно" }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    return { view, input };
  }

  function pick(input: HTMLInputElement, name: string) {
    const file = new File(["x"], name, { type: "image/jpeg" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    fireEvent.change(input);
  }

  it("(а) уход с экрана освобождает последний адрес", async () => {
    const urls = trackObjectUrls();
    const { view, input } = await atPhotoStep();

    pick(input, "one.jpg");
    // Присутствие первым: адрес создан, иначе «освобождён» было бы
    // правдой на пустом месте.
    await waitFor(() => expect(urls.created.length).toBeGreaterThan(0));

    view.unmount();
    await waitFor(() => expect(urls.alive()).toEqual([]));
  });

  it("(б) один выбор оставляет ровно один живой адрес — даже под StrictMode", async () => {
    const urls = trackObjectUrls();
    const { input } = await atPhotoStep();

    pick(input, "one.jpg");
    await waitFor(() => expect(urls.created.length).toBeGreaterThan(0));

    // `StrictMode` вызывает обновление состояния ДВАЖДЫ. Пока освобождение
    // стоит внутри обновления, второй вызов создаёт второй адрес, а
    // сохраняется один — и разница между созданным и живым растёт.
    expect(urls.alive()).toHaveLength(1);
  });

  // Имя уточнено по ревью: до листа работало освобождение СОХРАНЁННОГО
  // адреса. Сирота, которую оставлял `StrictMode`, не освобождалась
  // ничем — поэтому на старом коде этот узел тоже краснел, хоть и по
  // другой причине.
  it("второй выбор освобождает сохранённый адрес первого", async () => {
    const urls = trackObjectUrls();
    const { input } = await atPhotoStep();

    pick(input, "one.jpg");
    await waitFor(() => expect(urls.created.length).toBeGreaterThan(0));
    const first = urls.alive()[0];
    expect(first).toBeTruthy();

    pick(input, "two.jpg");
    await waitFor(() => expect(urls.revoked).toContain(first));
  });
});
