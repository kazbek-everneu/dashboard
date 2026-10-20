import { useAuthStore } from "@/store/auth"

// The API sends every timestamp as UTC ("…Z"). Times are shown in the user's
// profile time zone, or in the browser's own zone when none is set.

/** The browser's own IANA time zone. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/** The zone times are shown in: the profile's choice, else the browser's. */
export function getDisplayTimeZone(): string {
  return useAuthStore.getState().user?.timezone ?? browserTimeZone()
}

/** All zones offered in the profile picker (browsers leave out "UTC"). */
export function supportedTimeZones(): string[] {
  const zones = Intl.supportedValuesOf("timeZone")
  return zones.includes("UTC") ? zones : ["UTC", ...zones]
}

const ZONE_SUFFIX = /(Z|[+-]\d{2}:?\d{2})$/i

/** Parse an API timestamp; one without an offset is read as UTC, never local. */
export function parseApiDate(value: string): Date {
  return new Date(ZONE_SUFFIX.test(value) ? value : `${value}Z`)
}

/** Milliseconds since the epoch, for sorting and durations. */
export function apiDateMs(value: string): number {
  return parseApiDate(value).getTime()
}

const formatCache = new Map<string, Intl.DateTimeFormat>()

function cachedFormat(timeZone: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${timeZone}|${JSON.stringify(options)}`
  let format = formatCache.get(key)
  if (!format) {
    try {
      format = new Intl.DateTimeFormat("en-US", { ...options, timeZone })
    } catch {
      // A zone this browser does not know: fall back to its own.
      format = new Intl.DateTimeFormat("en-US", { ...options, timeZone: browserTimeZone() })
    }
    formatCache.set(key, format)
  }
  return format
}

const DATE_TIME: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
}

/** "Oct 20, 2026, 15:42" in the display time zone. */
export function formatDateTime(value: string, timeZone = getDisplayTimeZone()): string {
  return cachedFormat(timeZone, DATE_TIME).format(parseApiDate(value))
}

/** "Oct 20, 2026, 15:42 GMT+5" — the same with the zone, for tooltips. */
export function formatDateTimeWithZone(value: string, timeZone = getDisplayTimeZone()): string {
  return cachedFormat(timeZone, { ...DATE_TIME, timeZoneName: "short" }).format(parseApiDate(value))
}

/** "just now", "5m ago", "3h ago", "yesterday", "4d ago", then "Oct 20". */
export function formatRelativeTime(value: string, timeZone = getDisplayTimeZone()): string {
  const diffSec = Math.floor((Date.now() - apiDateMs(value)) / 1000)
  if (diffSec < 60) return "just now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffHr < 48) return "yesterday"
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay < 30) return `${diffDay}d ago`
  return cachedFormat(timeZone, { month: "short", day: "numeric" }).format(parseApiDate(value))
}
