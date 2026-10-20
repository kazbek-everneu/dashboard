import { getDeviceType } from "@/lib/helpers"
import type { CaptureWarning, KnownCaptureWarningCode, Screenshot } from "@/types"

// ─── Device type ─────────────────────────────────────────────────────────────

export type DeviceType = "desktop" | "tablet" | "mobile"

const DEVICE_TYPES: readonly string[] = ["desktop", "tablet", "mobile"]

export const DEVICE_LABELS: Record<DeviceType, string> = {
  desktop: "Desktop",
  tablet: "Tablet",
  mobile: "Mobile",
}

/** The device profile a screenshot was captured with. Rows from before device
 *  emulation have no device_type; theirs is inferred from the width. */
export function screenshotDeviceType(s: Pick<Screenshot, "device_type" | "width">): DeviceType {
  const declared = s.device_type
  return declared && DEVICE_TYPES.includes(declared) ? declared : getDeviceType(s.width)
}

// ─── Capture warnings ────────────────────────────────────────────────────────

export interface ScreenshotWarnings {
  /** Level "warning": the frame may differ from what a visitor sees. */
  warnings: CaptureWarning[]
  /** Level "info": what the capture did to get a clean frame. */
  notes: CaptureWarning[]
}

const NO_WARNINGS: ScreenshotWarnings = { warnings: [], notes: [] }

/**
 * A screenshot's capture warnings, split by level, in the order the capture
 * reported them. Only a saved frame (status "ok") has any: a failed row shows
 * its error instead, and a re-capturing row's list belongs to the frame being
 * replaced.
 *
 * Rows from before capture warnings flag an unstable DOM only through
 * captured_with_unstable_dom, with the text in error_message; they get a
 * synthetic dom_unstable warning carrying that text.
 */
export function getScreenshotWarnings(
  s: Pick<Screenshot, "status" | "warnings" | "captured_with_unstable_dom" | "error_message">,
): ScreenshotWarnings {
  if (s.status !== "ok") return NO_WARNINGS
  const reported = Array.isArray(s.warnings) ? s.warnings : []
  const legacyText = s.captured_with_unstable_dom ? s.error_message : null
  const all: CaptureWarning[] =
    legacyText && !reported.some((w) => w.code === "dom_unstable")
      ? [...reported, { code: "dom_unstable", level: "warning", message: legacyText, details: {} }]
      : reported
  if (all.length === 0) return NO_WARNINGS
  return {
    warnings: all.filter((w) => w.level !== "info"),
    notes: all.filter((w) => w.level === "info"),
  }
}

interface WarningLabel {
  title: string
  /** One line on what the warning means for the frame. */
  description: string
}

const WARNING_LABELS: Record<KnownCaptureWarningCode, WarningLabel> = {
  http_error: {
    title: "HTTP error",
    description: "The page answered with an error status, so the frame may show an error page.",
  },
  redirected: {
    title: "Redirected",
    description: "The page redirected to another URL before it was captured.",
  },
  challenge_passed: {
    title: "Bot check passed",
    description: "A bot-protection check appeared and cleared before the capture.",
  },
  fonts_timeout: {
    title: "Fonts timed out",
    description: "Some web fonts did not load in time; text may use a fallback font.",
  },
  assets_pending: {
    title: "Images still loading",
    description: "Some visible images were still loading when the frame was taken.",
  },
  assets_missing: {
    title: "Broken images",
    description: "Some images or backgrounds did not load, even after a retry.",
  },
  styles_missing: {
    title: "Missing stylesheets",
    description: "Some stylesheets did not load, so the page may look unstyled.",
  },
  assets_recovered: {
    title: "Assets recovered",
    description: "Images or stylesheets failed at first and loaded on a retry.",
  },
  page_reloaded: {
    title: "Page reloaded",
    description: "The page was reloaded once to recover resources that failed.",
  },
  recovery_skipped: {
    title: "Recovery skipped",
    description:
      "A recovery step did not run (not enough time left) or its result was not used (the reload failed), so the frame is the earlier one.",
  },
  dom_unstable: {
    title: "Page still changing",
    description: "The page kept changing during the capture; content may be incomplete.",
  },
  overflow_cropped: {
    title: "Cropped to viewport",
    description: "The page was wider than the viewport; the frame was cropped to the viewport width.",
  },
  screenshot_retried: {
    title: "Screenshot retried",
    description: "Taking the screenshot timed out once and was retried.",
  },
  inner_scroll_expanded: {
    title: "Inner scroll expanded",
    description: "The page scrolls inside a container, which was expanded to capture all content.",
  },
  infinite_scroll_stopped: {
    title: "Infinite scroll stopped",
    description: "The page kept growing while scrolling; the frame may end before the page does.",
  },
  overlays_hidden: {
    title: "Overlays hidden",
    description: "Cookie banners, chat widgets or pop-ups were hidden.",
  },
  carousels_paused: {
    title: "Carousels paused",
    description: "Sliders were stopped on their first slide.",
  },
}

/** Whether this frontend has its own title and description for the code. */
export function isKnownWarningCode(code: string): code is KnownCaptureWarningCode {
  return Object.hasOwn(WARNING_LABELS, code)
}

/** Short title: the label for the code (an HTTP error names its status), else
 *  the code itself for codes this frontend doesn't know yet. */
export function warningTitle(w: CaptureWarning): string {
  if (w.code === "http_error") {
    const status = asNumber(w.details?.status)
    if (status !== undefined) return `HTTP ${status}`
  }
  return isKnownWarningCode(w.code) ? WARNING_LABELS[w.code].title : humanize(w.code)
}

/** One line on what the warning means; unknown codes use the backend's message. */
export function warningDescription(w: CaptureWarning): string {
  return isKnownWarningCode(w.code) ? WARNING_LABELS[w.code].description : w.message
}

/** Titles joined for a one-line summary: "HTTP 404 · Broken images". */
export function summarizeWarnings(warnings: CaptureWarning[]): string {
  return warnings.map(warningTitle).join(" · ")
}

/** How many times the code was reported (repeats merge into one warning), but at
 *  least the number of samples or URLs it lists. */
export function warningCount(w: CaptureWarning): number {
  const details = w.details
  return Math.max(
    1,
    asNumber(details?.count) ?? 1,
    asArray(details?.samples).length,
    asArray(details?.urls).length,
  )
}

// ─── Reading untyped JSON (warning details, capture_meta) ─────────────────────

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined
}

export function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** A string list — a single string counts as a list of one. */
export function asStrings(value: unknown): string[] {
  if (typeof value === "string") return value.trim() ? [value] : []
  return asArray(value).filter((item): item is string => typeof item === "string" && item.trim() !== "")
}

// ─── Formatting ──────────────────────────────────────────────────────────────

/** "850 ms", "1.2 s", "14 s". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  const seconds = ms / 1000
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`
}

/** "fonts_timeout" → "Fonts timeout". */
export function humanize(value: string): string {
  const text = value.replace(/[_-]+/g, " ").trim()
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : value
}

function httpUrl(value: string): URL | null {
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:" ? url : null
  } catch {
    return null
  }
}

/** A link target only for http(s) URLs: sample URLs come from captured pages and
 *  must never become javascript: or data: links. */
export function safeHref(value: string | undefined): string | undefined {
  return value ? httpUrl(value)?.href : undefined
}

/** The file name ("hero.jpg"), else the host; non-http values are cut short. */
export function shortUrl(value: string): string {
  const url = httpUrl(value)
  if (!url) return value.length > 48 ? `${value.slice(0, 47)}…` : value
  const name = url.pathname.split("/").filter(Boolean).pop()
  if (!name) return url.host
  try {
    return decodeURIComponent(name)
  } catch {
    return name
  }
}

/** Same address once normalised (host case, default port, empty path). */
export function isSameUrl(a: string, b: string): boolean {
  try {
    return new URL(a).href === new URL(b).href
  } catch {
    return a === b
  }
}
