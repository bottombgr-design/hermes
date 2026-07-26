import { describe, expect, it } from "vitest";
import { en } from "@/i18n/en";
import { resolvePageTitle } from "./resolve-page-title";

describe("resolvePageTitle", () => {
  it("uses root dashboard plugin metadata before the Sessions fallback", () => {
    expect(
      resolvePageTitle("/", en, [{ path: "/", label: "Command Center" }]),
    ).toBe("Command Center");
  });

  it("keeps the historical Sessions fallback without a root plugin", () => {
    expect(resolvePageTitle("/", en, [])).toBe(en.app.nav.sessions);
  });
});
