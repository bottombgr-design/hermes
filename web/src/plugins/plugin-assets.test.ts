import { describe, expect, it } from "vitest";
import type { PluginManifest } from "./types";
import { pluginAssetUrl } from "./plugin-assets";

function manifest(version: string): PluginManifest {
  return {
    name: "example plugin",
    label: "Example",
    description: "",
    icon: "Puzzle",
    version,
    tab: { path: "/example", position: "end" },
    slots: [],
    entry: "dist/index.js",
    has_api: false,
    source: "user",
  };
}

describe("pluginAssetUrl", () => {
  it("versions production plugin assets from the manifest", () => {
    expect(pluginAssetUrl("", manifest("1.2.3+build"), "dist/index.js"))
      .toBe("/dashboard-plugins/example plugin/dist/index.js?v=1.2.3%2Bbuild");
  });

  it("preserves existing asset query parameters", () => {
    expect(pluginAssetUrl("/hermes", manifest("2"), "style.css?theme=dark"))
      .toBe("/hermes/dashboard-plugins/example plugin/style.css?theme=dark&v=2");
  });

  it("keeps the legacy URL when no version is declared", () => {
    expect(pluginAssetUrl("", manifest("  "), "dist/index.js"))
      .toBe("/dashboard-plugins/example plugin/dist/index.js");
  });
});
