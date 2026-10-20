import { useAuthStore } from "@/store/auth"
import {
  browserTimeZone,
  formatDateTime,
  formatDateTimeWithZone,
  formatRelativeTime,
  parseApiDate,
} from "@/lib/datetime"

interface DateTimeProps {
  /** An API timestamp (UTC). */
  value: string
  /** Show "5m ago" instead of the full date. */
  relative?: boolean
  className?: string
}

/** A timestamp in the viewer's time zone; hovering shows it with the zone. */
export function DateTime({ value, relative = false, className }: DateTimeProps) {
  // Subscribing re-renders every date as soon as the profile time zone changes.
  const timeZone = useAuthStore((s) => s.user?.timezone) ?? browserTimeZone()
  return (
    <time
      dateTime={parseApiDate(value).toISOString()}
      title={formatDateTimeWithZone(value, timeZone)}
      className={className}
    >
      {relative ? formatRelativeTime(value, timeZone) : formatDateTime(value, timeZone)}
    </time>
  )
}
