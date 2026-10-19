import type { Screenshot } from "@/types"

const API_BASE = import.meta.env.VITE_API_URL ?? "/api"

export function buildImgUrl(screenshot: Screenshot): string {
  const base = `${API_BASE}${screenshot.signed_url}`
  // Cache-bust by created_at: a re-capture overwrites the same file at the same
  // signed URL, so without this the browser would keep showing the stale image.
  // serve_file only reads `expires`/`token`, so the extra `v` param is ignored.
  const separator = base.includes("?") ? "&" : "?"
  return `${base}${separator}v=${encodeURIComponent(screenshot.created_at)}`
}

const MAX_STRIP_WIDTH = 480
const REFERENCE_WIDTH = 1920

export function getRenderedStripDimensions(screenshot: Screenshot): {
  width: number
  height: number
} {
  const actualWidth = screenshot.image_width ?? screenshot.width
  const actualHeight = screenshot.image_height ?? screenshot.height

  const scale = MAX_STRIP_WIDTH / Math.max(REFERENCE_WIDTH, actualWidth)
  return {
    width: Math.round(actualWidth * scale),
    height: Math.round(actualHeight * scale),
  }
}
