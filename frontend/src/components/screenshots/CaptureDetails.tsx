import { Fragment } from "react"
import { ChevronDown, Monitor, Smartphone, Tablet } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import {
  DEVICE_LABELS,
  asNumber,
  formatMs,
  humanize,
  isRecord,
  isSameUrl,
  safeHref,
  screenshotDeviceType,
  type DeviceType,
} from "@/lib/screenshot-meta"
import type { CaptureMeta, Screenshot } from "@/types"

const DEVICE_ICONS = { desktop: Monitor, tablet: Tablet, mobile: Smartphone }

/** timings_ms keys in pipeline order, with their labels. */
const PHASES = new Map([
  ["context", "Browser context"],
  ["goto", "Navigation"],
  ["load_wait", "Load event"],
  ["quiet", "Network quiet"],
  ["prepare", "Page preparation"],
  ["scroll", "Scrolling"],
  ["readiness", "Readiness"],
  ["recovery", "Recovery"],
  ["reload", "Reload"],
  ["snapshot", "Screenshot"],
])
const PHASE_KEYS = [...PHASES.keys()]

const LABEL = "text-muted-foreground"

/**
 * Capture metadata as rows for a two-column `<dl>` grid (the Capture card of
 * the screenshot page): device and emulation, user agent, HTTP status, final
 * URL, attempts, readiness, hidden overlays, failure and the phase timings.
 * Rows without data are left out, so a row from before capture metadata only
 * gets its device, inferred from the width.
 */
export function CaptureDetails({ screenshot: s }: { screenshot: Screenshot }) {
  const meta: CaptureMeta = s.capture_meta ?? {}
  const { device, navigation, readiness, overlays, failure } = meta

  const deviceType = screenshotDeviceType(s)
  const DeviceIcon = DEVICE_ICONS[deviceType]
  const emulation = emulationNote(deviceType, device?.emulation, s.device_type == null)
  const httpStatus = asNumber(navigation?.http_status)
  const finalUrl =
    navigation?.final_url && !isSameUrl(navigation.final_url, navigation.requested_url ?? s.url)
      ? navigation.final_url
      : null
  const attempts = asNumber(meta.attempts)
  const readinessText = readinessNote(readiness)
  const hiddenOverlays = asNumber(overlays?.hidden)
  const failureClass = failure?.class
  const timings: Record<string, unknown> = isRecord(meta.timings_ms) ? meta.timings_ms : {}
  const totalMs = asNumber(timings.total)
  const phases = phaseTimings(timings)

  return (
    <>
      <dt className={LABEL}>Device</dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-x-1.5">
        <DeviceIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {DEVICE_LABELS[deviceType]}
        {emulation && (
          <span className="text-xs text-muted-foreground" title={emulation.hint}>
            · {emulation.text}
          </span>
        )}
      </dd>

      {device && (
        <>
          <dt className={LABEL}>User agent</dt>
          <dd className="min-w-0">
            {device.user_agent ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    tabIndex={0}
                    className="block truncate rounded-sm font-mono text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    {device.user_agent}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="font-mono break-all">{device.user_agent}</TooltipContent>
              </Tooltip>
            ) : (
              <span className="text-xs text-muted-foreground">Browser default</span>
            )}
          </dd>
        </>
      )}

      {httpStatus !== undefined && (
        <>
          <dt className={LABEL}>HTTP status</dt>
          <dd
            className={cn(
              "font-mono text-xs tabular-nums",
              httpStatus >= 400 && "font-medium text-status-warning-fg",
            )}
          >
            {httpStatus}
          </dd>
        </>
      )}

      {finalUrl && (
        <>
          <dt className={cn(LABEL, "self-start")}>Final URL</dt>
          <dd className="min-w-0">
            <ExternalUrl url={finalUrl} />
          </dd>
        </>
      )}

      {attempts !== undefined && (
        <>
          <dt className={LABEL}>Attempts</dt>
          <dd className="tabular-nums">{attempts}</dd>
        </>
      )}

      {readinessText && (
        <>
          <dt className={LABEL}>Readiness</dt>
          <dd
            className={cn(readinessText.unstable && "text-status-warning-fg")}
            title={readinessText.hint}
          >
            {readinessText.text}
          </dd>
        </>
      )}

      {hiddenOverlays !== undefined && hiddenOverlays > 0 && (
        <>
          <dt className={LABEL}>Overlays</dt>
          <dd className="tabular-nums">{hiddenOverlays} hidden</dd>
        </>
      )}

      {failureClass && (
        <>
          <dt className={cn(LABEL, "self-start")}>Failure</dt>
          <dd className="min-w-0 text-xs break-words">
            <span className="font-mono">{failureClass}</span>
            {failure?.phase && (
              <span className="text-muted-foreground">
                {" "}
                in {phaseLabel(failure.phase).toLowerCase()}
              </span>
            )}
          </dd>
        </>
      )}

      {(phases.length > 0 || totalMs !== undefined) && (
        <>
          <dt className={cn(LABEL, "self-start")}>Timings</dt>
          <dd className="min-w-0">
            {phases.length === 0 ? (
              <span className="tabular-nums">{formatMs(totalMs ?? 0)} total</span>
            ) : (
              <Collapsible>
                <CollapsibleTrigger className="group inline-flex items-center gap-1 rounded-sm tabular-nums outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
                  {totalMs !== undefined ? `${formatMs(totalMs)} total` : "By phase"}
                  <ChevronDown
                    className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
                    aria-hidden="true"
                  />
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <dl className="mt-1.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-xs">
                    {phases.map((phase) => (
                      <Fragment key={phase.key}>
                        <dt className="truncate text-muted-foreground">{phase.label}</dt>
                        <dd className="text-right tabular-nums">{formatMs(phase.ms)}</dd>
                      </Fragment>
                    ))}
                  </dl>
                </CollapsibleContent>
              </Collapsible>
            )}
          </dd>
        </>
      )}
    </>
  )
}

function ExternalUrl({ url }: { url: string }) {
  const href = safeHref(url)
  const urlClass = "font-mono text-xs leading-5 break-all"
  return href ? (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(urlClass, "underline-offset-2 hover:underline")}
    >
      {url}
    </a>
  ) : (
    <span className={urlClass}>{url}</span>
  )
}

/** What the capture emulated, next to the device type. Desktop needs no note;
 *  a mobile or tablet row from before emulation was a narrow desktop window. */
function emulationNote(
  deviceType: DeviceType,
  emulation: string | undefined,
  legacy: boolean,
): { text: string; hint?: string } | null {
  switch (emulation) {
    case "full":
      return { text: "full emulation", hint: "Mobile viewport, touch and a mobile user agent." }
    case "partial":
      return {
        text: "partial emulation",
        hint: "Touch and a mobile user agent only: this browser can't emulate a mobile viewport.",
      }
    case "none":
      return deviceType === "desktop"
        ? null
        : { text: "no emulation", hint: "Captured in a desktop browser window of this size." }
    case undefined:
      return legacy && deviceType !== "desktop"
        ? {
            text: "no emulation",
            hint: "Captured before device emulation, in a desktop browser window of this size.",
          }
        : null
    default:
      return { text: emulation }
  }
}

function readinessNote(
  readiness: CaptureMeta["readiness"],
): { text: string; unstable: boolean; hint?: string } | null {
  if (!readiness) return null
  const parts: string[] = []
  if (readiness.stable === true) parts.push("stable")
  if (readiness.stable === false) parts.push("unstable")
  if (readiness.fast_path) parts.push("fast path")
  if (readiness.second_chance) parts.push("second chance")
  if (readiness.fonts_ready === false) parts.push("fonts not ready")
  if (parts.length === 0) return null

  const hints = [
    readiness.fast_path && "Fast path: the page went quiet early, so the full stability wait was skipped.",
    readiness.second_chance &&
      "Second chance: the first stability check timed out and was repeated on the same page.",
  ].filter(Boolean)
  const text = parts.join(" · ")
  return {
    text: text.charAt(0).toUpperCase() + text.slice(1),
    unstable: readiness.stable === false,
    hint: hints.length > 0 ? hints.join("\n") : undefined,
  }
}

function phaseLabel(key: string): string {
  return PHASES.get(key) ?? humanize(key)
}

/** Phase durations in pipeline order (unknown phases after), without the total. */
function phaseTimings(timings: Record<string, unknown>): { key: string; label: string; ms: number }[] {
  const rank = (key: string) => {
    const index = PHASE_KEYS.indexOf(key)
    return index === -1 ? PHASE_KEYS.length : index
  }
  return Object.entries(timings)
    .flatMap(([key, value]) => {
      const ms = asNumber(value)
      return key !== "total" && ms !== undefined ? [{ key, label: phaseLabel(key), ms }] : []
    })
    .sort((a, b) => rank(a.key) - rank(b.key))
}
