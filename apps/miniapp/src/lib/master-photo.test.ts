/**
 * DRF-2539 — фото мастера и работы портфолио: только наш прокси, с подписью.
 *
 * Держит:
 * * грузим только путь прокси; адрес хранилища (старый провод) и любой
 *   чужой адрес — «фото нет» без запроса, подпись никуда не уходит;
 * * запрос — с заголовком initData (без него прокси отвечает 401);
 * * 404 — «фото нет», а не ошибка;
 * * одна картинка — один запрос на все карточки; свободные картинки
 *   переживают уход экрана (витрина ↔ профиль), вытесняются самые давние.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("./max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import {
  acquireMasterPhoto,
  loadMasterPhoto,
  MASTER_MEDIA_PREFIX,
  masterPhotoPath,
  MAX_CACHED_MASTER_PHOTOS,
  resetMasterPhotoCacheForTests,
} from "./master-photo";

const fetchMock = vi.fn();

const PHOTO = "/api/v1/customer/media/masters/7b0c3f7e-1f7a-4a53-9a55-0d4d1f5d6a11/photo?v=3f2a9c1b7d4e";
const WORK =
  "/api/v1/customer/media/masters/7b0c3f7e-1f7a-4a53-9a55-0d4d1f5d6a11/portfolio/0f5e2d1c-9b8a-4c3d-8e7f-6a5b4c3d2e1f/image?v=a1b2c3d4e5f6";
/** Форма значения до DRF-2539 — `FieldFile.url` с настройками стенда (prod.py). */
const STORAGE_URL =
  "http://minio:9000/beautygo-media/specialists/avatars/a.jpg?AWSAccessKeyId=KEY&Signature=SIG&Expires=1790000000";

let revokeObjectURL: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  let n = 0;
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:ayla/photo-${++n}`);
  revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
});

afterEach(() => {
  resetMasterPhotoCacheForTests();
});

function photoResponse(): Response {
  return new Response(new Uint8Array([0xff, 0xd8, 0xff]), {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

describe("грузим только путь нашего прокси", () => {
  it("префикс — зеркало MEDIA_PREFIX сервера", () => {
    expect(MASTER_MEDIA_PREFIX).toBe("/api/v1/customer/media/masters/");
    expect(masterPhotoPath(PHOTO)).toBe(PHOTO);
    expect(masterPhotoPath(WORK)).toBe(WORK);
  });

  it.each([
    ["пусто", ""],
    ["null", null],
    ["undefined", undefined],
    ["адрес хранилища (старый провод)", STORAGE_URL],
    ["абсолютный адрес на наш путь", `https://evil.example${PHOTO}`],
    ["протокол-относительный", `//evil.example${PHOTO}`],
    ["другая ручка бота", "/api/v1/customer/diary/entry/x/photo"],
    ["выход из префикса", "/api/v1/customer/media/masters/../../me"],
  ])("%s → null без единого запроса", async (_label, value) => {
    expect(masterPhotoPath(value)).toBeNull();
    await expect(loadMasterPhoto(value)).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("запрос — с подписью, ответ — blob", () => {
  it("идёт на наш путь с Authorization: MaxInitData", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    await expect(loadMasterPhoto(PHOTO)).resolves.toBe("blob:ayla/photo-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PHOTO);
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
  });

  it("404 → null, не ошибка", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: "not_found" }), { status: 404 }));
    await expect(loadMasterPhoto(PHOTO)).resolves.toBeNull();
  });

  it("502 → ApiError с кодом сервера", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "ayla_unavailable", detail: "catalog unavailable" }), {
        status: 502,
      }),
    );
    await expect(loadMasterPhoto(PHOTO)).rejects.toMatchObject({
      constructor: ApiError,
      status: 502,
      slug: "ayla_unavailable",
    });
  });
});

describe("кэш витрины", () => {
  it("одна картинка — один запрос на все карточки", async () => {
    fetchMock.mockResolvedValue(photoResponse());
    const a = acquireMasterPhoto(PHOTO);
    const b = acquireMasterPhoto(PHOTO);
    await expect(a.promise).resolves.toBe("blob:ayla/photo-1");
    await expect(b.promise).resolves.toBe("blob:ayla/photo-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    a.release();
    b.release();
  });

  it("уход экрана не убирает картинку: возврат — без запроса", async () => {
    fetchMock.mockResolvedValue(photoResponse());
    const first = acquireMasterPhoto(PHOTO);
    await first.promise;
    first.release();
    await new Promise((r) => setTimeout(r, 0));
    const again = acquireMasterPhoto(PHOTO);
    await expect(again.promise).resolves.toBe("blob:ayla/photo-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    again.release();
  });

  it("свободных больше предела — вытесняется самая давняя и её адрес освобождается", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(photoResponse()));
    const paths = Array.from(
      { length: MAX_CACHED_MASTER_PHOTOS + 1 },
      (_v, i) => `${MASTER_MEDIA_PREFIX}m-${i}/photo?v=1`,
    );
    for (const p of paths) {
      const lease = acquireMasterPhoto(p);
      await lease.promise;
      lease.release();
    }
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:ayla/photo-1");
    expect(resetMasterPhotoCacheForTests()).toBe(MAX_CACHED_MASTER_PHOTOS);
  });

  it("занятая картинка не вытесняется, сколько бы свободных ни пришло", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(photoResponse()));
    const held = acquireMasterPhoto(PHOTO);
    await held.promise;
    for (let i = 0; i <= MAX_CACHED_MASTER_PHOTOS; i += 1) {
      const lease = acquireMasterPhoto(`${MASTER_MEDIA_PREFIX}m-${i}/photo?v=1`);
      await lease.promise;
      lease.release();
    }
    expect(revokeObjectURL).not.toHaveBeenCalledWith("blob:ayla/photo-1");
    held.release();
  });

  it("сбой не кэшируется: следующий показ пробует снова", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 502 }));
    const failed = acquireMasterPhoto(PHOTO);
    await expect(failed.promise).rejects.toBeInstanceOf(ApiError);
    failed.release();
    fetchMock.mockResolvedValueOnce(photoResponse());
    const retry = acquireMasterPhoto(PHOTO);
    await expect(retry.promise).resolves.toBe("blob:ayla/photo-1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    retry.release();
  });
});
