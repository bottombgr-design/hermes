import type { PluginManifest } from "./types";

/** Build a dashboard plugin asset URL that changes when the plugin does. */
export function pluginAssetUrl(
  basePath: string,
  manifest: PluginManifest,
  assetPath: string,
): string {
  const url = `${basePath}/dashboard-plugins/${manifest.name}/${assetPath}`;
  const version = manifest.version?.trim();
  if (!version) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(version)}`;
}
