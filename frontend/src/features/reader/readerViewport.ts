export type ReaderFitMode = "comfortable" | "page" | "width";

export const DEFAULT_READER_FIT_MODE: ReaderFitMode = "comfortable";
export const READER_ZOOM_MIN = 75;
export const READER_ZOOM_MAX = 200;
export const READER_ZOOM_STEP = 25;
export const MOBILE_READER_MAX_WIDTH = 720;

const COMFORTABLE_VIEWPORT_FRACTION = 0.88;
const COMFORTABLE_MAX_HEIGHT = 900;

interface PageDimensions {
  readonly width: number;
  readonly height: number;
}

export interface ReaderViewportMetrics {
  readonly width: number;
  readonly height: number;
}

export function isReaderFitMode(value: unknown): value is ReaderFitMode {
  return value === "comfortable" || value === "page" || value === "width";
}

export function clampReaderZoom(zoom: number): number {
  return Math.min(READER_ZOOM_MAX, Math.max(READER_ZOOM_MIN, zoom));
}

export function isMobileReaderViewport(width: number): boolean {
  return Math.max(1, width) <= MOBILE_READER_MAX_WIDTH;
}

export function effectiveReaderFitMode(fitMode: ReaderFitMode, viewportWidth: number): ReaderFitMode {
  if (fitMode === "comfortable" && isMobileReaderViewport(viewportWidth)) {
    return "width";
  }
  return fitMode;
}

export function calculateReaderCanvasWidth(
  dimensions: PageDimensions,
  viewport: ReaderViewportMetrics,
  fitMode: ReaderFitMode,
  zoom: number,
): number {
  const pageWidth = Math.max(1, dimensions.width);
  const pageHeight = Math.max(1, dimensions.height);
  const viewportWidth = Math.max(1, viewport.width);
  const viewportHeight = Math.max(1, viewport.height);
  const aspectRatio = pageWidth / pageHeight;
  const effectiveFitMode = effectiveReaderFitMode(fitMode, viewportWidth);

  const fitPageWidth = Math.min(viewportWidth, viewportHeight * aspectRatio);
  const comfortableHeight = Math.min(
    viewportHeight * COMFORTABLE_VIEWPORT_FRACTION,
    COMFORTABLE_MAX_HEIGHT,
  );
  const comfortableWidth = Math.min(viewportWidth, comfortableHeight * aspectRatio);

  const fittedWidth = effectiveFitMode === "width"
    ? viewportWidth
    : effectiveFitMode === "page"
      ? fitPageWidth
      : comfortableWidth;

  return fittedWidth * (clampReaderZoom(zoom) / 100);
}
