/**
 * Отказ каталога на фото доезжает целиком, а не одной общей фразой (DRF-2439).
 *
 * ### Единственное живое место из восьми
 *
 * Перепись DRF-2373 нашла 12 конструкторов `ApiError` в 6 файлах; восемь
 * роняли `details`. Семь из восьми — потери пустые: сервер на тех ручках
 * поля не присылает. **Это — не пустая.**
 *
 * `PATCH /api/v1/master/profile` отвечает через `_profile_refusal`
 * (`apps/master_api/views.py:849`) и на 400 кладёт в `details` данные
 * каталога о том, **что именно** не так с фото — формат, квадрат, размер.
 * Человек видел только общее «Каталог не принял профиль.».
 *
 * ### Почему это ровно тот же дефект, что чинили в DRF-2373
 *
 * Одна ручка обслуживает и текст, и multipart. Текстовый путь идёт через
 * общего помощника `request` и поле получает; путь с фото пишет свой `fetch`
 * (иначе браузер не поставит multipart boundary) — и ронял.
 *
 * DRF-2273 «починил клиент», но починил **функцию-помощник**, а не правило.
 * Ни один самописный `fetch` под ту правку не попал.
 *
 * ### Что здесь НЕ проверяется
 *
 * Что экран показывает подробность человеку: сегодня ни один экран
 * `details` для профиля не читает, а рисовать их значило бы сочинять
 * видимый текст. Лист довозит **уже присланное** сервером до границы
 * клиента; показ — отдельное решение и отдельные слова.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { uploadMasterProfilePhoto } from "./master-api";

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function photo(): File {
  return new File([new Uint8Array([1, 2, 3])], "photo.jpg", {
    type: "image/jpeg",
  });
}

/** Форма, которой отвечает `_profile_refusal` на 400. */
const CATALOG_REFUSAL = {
  error: "validation_error",
  detail: "Каталог не принял профиль.",
  details: { photo: ["Фото должно быть квадратным."] },
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadMasterProfilePhoto", () => {
  it("доносит details каталога, а не только общую фразу", async () => {
    fetchMock.mockResolvedValue(jsonResponse(400, CATALOG_REFUSAL));

    const err = await uploadMasterProfilePhoto(photo()).catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).details).toEqual(CATALOG_REFUSAL.details);
  });

  it("прежние поля на месте — details добавлены, а не подменили тело", async () => {
    // Положительная стража: без неё «details пришли» зеленело бы и на
    // отказе, потерявшем текст и слаг.
    fetchMock.mockResolvedValue(jsonResponse(400, CATALOG_REFUSAL));

    const err = (await uploadMasterProfilePhoto(photo()).catch(
      (e) => e,
    )) as ApiError;

    expect(err.status).toBe(400);
    expect(err.slug).toBe("validation_error");
    expect(err.detail).toBe("Каталог не принял профиль.");
  });

  it("отказ без details не выдумывает их", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(503, {
        error: "catalog_unavailable",
        detail: "Каталог сейчас недоступен — попробуйте позже.",
      }),
    );

    const err = (await uploadMasterProfilePhoto(photo()).catch(
      (e) => e,
    )) as ApiError;

    expect(err.details).toBeUndefined();
    expect(err.detail).toBe("Каталог сейчас недоступен — попробуйте позже.");
  });
});
