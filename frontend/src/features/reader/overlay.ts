export interface NormalizedBox {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface ImageDimensions {
  readonly width: number;
  readonly height: number;
}

export interface SvgBox {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export function toSvgBox(box: NormalizedBox, image: ImageDimensions): SvgBox {
  return {
    x: box.x * image.width,
    y: box.y * image.height,
    width: box.width * image.width,
    height: box.height * image.height,
  };
}
