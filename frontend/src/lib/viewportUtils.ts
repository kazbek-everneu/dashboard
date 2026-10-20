import type { ViewportConfig } from "@/types"

function detectDeviceType(width: number): ViewportConfig["device_type"] {
  if (width >= 1024) return "desktop"
  if (width >= 768) return "tablet"
  return "mobile"
}

/** Normalise a raw viewport dict from the API — fills in missing fields. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function normalizeViewport(vp: any): ViewportConfig {
  const width = (vp.width as number) ?? 1920
  const height = (vp.height as number) ?? 1080
  const device_type =
    (vp.device_type as ViewportConfig["device_type"] | undefined) ?? detectDeviceType(width)
  const browsers = (vp.browsers as string[] | null | undefined) ?? null
  return { width, height, device_type, browsers }
}

/** Shown with the viewport lists: the device type decides how a viewport is captured. */
export const DEVICE_EMULATION_HINT =
  "Tablet and mobile viewports are captured with device emulation (touch, mobile user agent)."
