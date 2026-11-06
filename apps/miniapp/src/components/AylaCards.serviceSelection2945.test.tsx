import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AylaCards } from "./AylaCards";

describe("DRF-2945 · ambiguous service selection", () => {
  it("does not guess a service and sends the exact service_id only after the user chooses", async () => {
    const onSelect = vi.fn();
    render(
      <AylaCards
        cards={[
          {
            kind: "choose_service",
            options: [
              { service_id: "svc-1", name: "Классический массаж", duration_min: 60 },
              { service_id: "svc-2", name: "Спортивный массаж", duration_min: 60 },
            ],
          },
        ]}
        onSelect={onSelect}
        onRecheck={vi.fn()}
      />,
    );

    expect(onSelect).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: /Спортивный массаж/ }),
    );

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("Спортивный массаж", {
      service_id: "svc-2",
    });
  });
});
