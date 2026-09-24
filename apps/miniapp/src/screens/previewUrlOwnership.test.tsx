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
 * освобождается. Поправка листа: это **один** адрес на посещение экрана, а
 * не «по одному на каждый выбор» — освобождение предыдущего при новом
 * выборе есть и работает.
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
import { type ScanResponse } from "../lib/food-scanner";
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

/** Счётчик адресов: что создано, что отозвано и что ещё живо. */
function trackObjectUrls() {
  let n = 0;
  const created: string[] = [];
  const revoked: string[] = [];
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: (obj: unknown) => {
      void obj;
      const url = `blob:test/${++n}`;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url: string) => {
      revoked.push(url);
    },
  });
  return {
    created,
    revoked,
    alive: () => created.filter((u) => !revoked.includes(u)),
  };
}

function renderResult(photo: File) {
  return render(
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
    </MemoryRouter>,
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

  it("ПОВТОРНЫЙ показ: адрес снова живой — этот узел и ловит дефект", async () => {
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

  it("второй выбор освобождает первый — это работало и до листа", async () => {
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
