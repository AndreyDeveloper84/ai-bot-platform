/**
 * DRF-2455 — снимок записи дневника: только прокси, и только когда он есть.
 *
 * Держит четыре вещи:
 * * `has_photo` не `true` → к прокси не ходим вовсе (иначе 404 на каждой
 *   записи старше 30 суток);
 * * когда ходим — тем же заголовком initData, что все запросы: картинка
 *   без него получила бы отказ (узел k4 прокси);
 * * 404 — «фото нет», а не ошибка;
 * * оба экрана, рисующих записи (день — `/diary/day`, главная дневника —
 *   `/wellness/today?surface=diary`), получают признак одинаково: поле,
 *   поднятое в одном клиенте и потерянное в другом, у нас уже было.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import { getWellnessToday } from "./customer-wellness";
import { getDiaryDay } from "./diary-days";
import { diaryEntryPhotoPath, loadDiaryEntryPhoto } from "./diary-photo";

const fetchMock = vi.fn();
const createObjectURL = vi.fn(() => "blob:ayla/photo-1");

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const WITH_PHOTO = { id: "log-1", has_photo: true };
const WITHOUT_PHOTO = { id: "log-2", has_photo: false };
const LEGACY = { id: "log-3" };

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  createObjectURL.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL }));
});

describe("к прокси ходим только за снимком, который есть", () => {
  it.each([
    ["has_photo=false", WITHOUT_PHOTO],
    ["поля нет (старый ответ)", LEGACY],
  ])("%s → null без единого запроса", async (_label, entry) => {
    expect(diaryEntryPhotoPath(entry)).toBeNull();
    await expect(loadDiaryEntryPhoto(entry)).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("has_photo=true → один запрос к прокси с initData, картинке — blob-адрес", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(new Blob(["jpeg-bytes"], { type: "image/jpeg" }), { status: 200 }),
    );

    const src = await loadDiaryEntryPhoto(WITH_PHOTO);

    expect(src).toBe("blob:ayla/photo-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/customer/diary/entry/log-1/photo");
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("404 — «фото нет», не ошибка", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "not_found", detail: "" }, 404));

    await expect(loadDiaryEntryPhoto(WITH_PHOTO)).resolves.toBeNull();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("другой отказ — ApiError, как у всех запросов", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: "boom", detail: "x" }, 502));

    await expect(loadDiaryEntryPhoto(WITH_PHOTO)).rejects.toBeInstanceOf(ApiError);
  });

  it("никакого адреса хранилища: путь строится только из id записи", () => {
    const path = diaryEntryPhotoPath({ id: "a/b?c", has_photo: true });
    expect(path).toBe("/diary/entry/a%2Fb%3Fc/photo");
    expect(path).not.toMatch(/minio|:9000|X-Amz|https?:/);
  });
});

describe("оба экрана дневника получают признак одинаково", () => {
  const WIRE_ENTRIES = [
    { ...WITH_PHOTO, dish_name: "Овсянка", calories: 340, meal_type: "breakfast", logged_at: "2026-09-26T08:14:00Z" },
    { ...WITHOUT_PHOTO, dish_name: "Суп", calories: null, meal_type: "lunch", logged_at: "2026-09-26T13:32:00Z" },
  ];

  it("день (/diary/day) и главная дневника (/wellness/today) несут has_photo насквозь", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ date: "2026-09-26", calories_total: 340, entries: WIRE_ENTRIES }),
      )
      .mockResolvedValueOnce(jsonResponse({ entries: WIRE_ENTRIES }));

    const day = await getDiaryDay("2026-09-26");
    const today = await getWellnessToday({ surface: "diary" });

    const paths = (entries: { id: string; has_photo?: boolean }[] | undefined) =>
      (entries ?? []).map((e) => [e.id, e.has_photo, diaryEntryPhotoPath(e)]);
    expect(paths(day.entries)).toEqual([
      ["log-1", true, "/diary/entry/log-1/photo"],
      ["log-2", false, null],
    ]);
    expect(paths(today.entries)).toEqual(paths(day.entries));
  });
});
