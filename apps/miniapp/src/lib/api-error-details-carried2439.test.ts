/**
 * Самописные `fetch` перестали быть местом, где теряется `details` (DRF-2439).
 *
 * ### Что здесь проверяется, а что нет
 *
 * На этих ручках сервер `details` **сегодня не присылает** — и узлы это не
 * утверждают. Они утверждают другое: **клиент больше не теряет поле**, если
 * оно придёт. Разница важна, потому что именно её три листа подряд путали:
 * потеря пустая сегодня и настоящая завтра, а замечают её только когда
 * кто-то читает два файла подряд.
 *
 * Единственное живое место из восьми — загрузка фото профиля мастера — стоит
 * отдельным файлом (`master-api.profile-photo-details2439.test.ts`), потому
 * что там сервер поле присылает и потеря была настоящей.
 *
 * ### Почему эти вызовы вообще мимо помощника
 *
 * DRF-2273 «починил клиент», но починил **функцию-помощник**
 * (`admin-api.ts::requestWithResponse`), а правило звучит как требование к
 * вызову. Все восемь оставшихся мест написаны руками — из-за multipart
 * (boundary ставит браузер) или потому что часть статусов для них исход, а
 * не ошибка (`decisionFetch` и 409). Ни одно под ту правку не попало.
 *
 * Поэтому правило теперь держит сторож на конструктор
 * (`tools/lint/api_error_details_guard.py`): он судит **вызов**, а не
 * функцию, и потому видит тех, кто помощника обходит.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { uploadPortfolioPhoto } from "./master-api";
import { exportPersonalData } from "./personal-data";

const fetchMock = vi.fn();

const DETAILS = { field: ["сервер однажды это пришлёт"] };

function refusal(): Response {
  return new Response(
    JSON.stringify({
      error: "validation_error",
      detail: "Не принято.",
      details: DETAILS,
    }),
    { status: 400, headers: { "Content-Type": "application/json" } },
  );
}

function photo(): File {
  return new File([new Uint8Array([1, 2, 3])], "photo.jpg", {
    type: "image/jpeg",
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadPortfolioPhoto — multipart мимо помощника", () => {
  it("доносит details, если сервер их пришлёт", async () => {
    fetchMock.mockResolvedValue(refusal());

    const err = (await uploadPortfolioPhoto(photo()).catch((e) => e)) as ApiError;

    expect(err).toBeInstanceOf(ApiError);
    expect(err.details).toEqual(DETAILS);
  });

  it("прежние поля на месте", async () => {
    // Положительная стража: «details пришли» зеленело бы и на отказе,
    // потерявшем текст и слаг.
    fetchMock.mockResolvedValue(refusal());

    const err = (await uploadPortfolioPhoto(photo()).catch((e) => e)) as ApiError;

    expect(err.slug).toBe("validation_error");
    expect(err.detail).toBe("Не принято.");
  });
});

describe("выгрузка личных данных — свой помощник целиком", () => {
  it("доносит details, если сервер их пришлёт", async () => {
    fetchMock.mockResolvedValue(refusal());

    const err = (await exportPersonalData().catch((e) => e)) as ApiError;

    expect(err).toBeInstanceOf(ApiError);
    expect(err.details).toEqual(DETAILS);
  });
});
