/** Compact numeric input used by the viewport rows (no spinner arrows). */
export const RESOLUTION_INPUT_CLASS =
  "h-7 w-[6ch] px-1 text-center tabular-nums [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"

export const WIDTH_RANGE_ERROR = "Width must be 320–3840"
export const HEIGHT_RANGE_ERROR = "Height must be 400–2160"

export function validateResolution(width: number, height: number) {
  return {
    width: !isNaN(width) && (width < 320 || width > 3840) ? WIDTH_RANGE_ERROR : undefined,
    height: !isNaN(height) && (height < 400 || height > 2160) ? HEIGHT_RANGE_ERROR : undefined,
  }
}
