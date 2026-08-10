import packageMetadata from "../package.json";

export const APPLICATION_VERSION = packageMetadata.version;

export function versionLabel(version: string): string {
  return `v${version}`;
}
