import { describe, expect, it } from "vitest";

import { formatDateTime } from "./dateTime";

describe("formatDateTime", () => {
  it("renders timestamps through seconds", () => {
    expect(formatDateTime("2026-01-01T00:01:02Z")).toMatch(/\d{2}:\d{2}:\d{2}/);
  });
});
