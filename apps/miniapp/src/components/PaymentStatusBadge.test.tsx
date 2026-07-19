/**
 * Tests for `PaymentStatusBadge` — the C7.3 read model in records and
 * detail surfaces. Renders the locked labels; hidden for
 * waiting_for_capture / unknown / absent (ADR).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaymentStatusBadge } from "./PaymentStatusBadge";

describe("PaymentStatusBadge", () => {
  it("renders the locked label for a visible state", () => {
    render(<PaymentStatusBadge state="authorized" />);
    expect(screen.getByText("Зарезервировано")).toBeInTheDocument();
  });

  it("renders «Оплата не прошла» for failed", () => {
    render(<PaymentStatusBadge state="failed" />);
    expect(screen.getByText("Оплата не прошла")).toBeInTheDocument();
  });

  it("renders nothing for waiting_for_capture (ADR-hidden)", () => {
    const { container } = render(<PaymentStatusBadge state="waiting_for_capture" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for unknown / absent states", () => {
    for (const bad of ["future_state", "", null, undefined]) {
      const { container, unmount } = render(<PaymentStatusBadge state={bad} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });
});
