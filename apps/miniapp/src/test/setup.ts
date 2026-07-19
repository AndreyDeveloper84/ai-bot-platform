/**
 * Vitest global setup — jsdom environment helpers.
 *
 * - `@testing-library/jest-dom/vitest` registers DOM matchers on vitest's
 *   `expect` (toBeInTheDocument, toHaveAttribute, …). Importing it here
 *   also pulls the type augmentation into the program, so matchers
 *   typecheck in every test file without tsconfig `types` surgery.
 * - Explicit `cleanup` per test — RTL auto-cleanup only wires itself when
 *   test globals are enabled; this project keeps vitest globals off and
 *   imports { describe/it/expect } explicitly in each test file.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
