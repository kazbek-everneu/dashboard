import type { ReactNode } from "react"
import { ChevronDown, Info } from "lucide-react"
import { cn } from "@/lib/utils"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { WarningAlert } from "@/components/ui/WarningAlert"
import {
  asArray,
  asNumber,
  asString,
  asStrings,
  formatMs,
  getScreenshotWarnings,
  humanize,
  isKnownWarningCode,
  isRecord,
  safeHref,
  shortUrl,
  summarizeWarnings,
  warningCount,
  warningDescription,
  warningTitle,
} from "@/lib/screenshot-meta"
import type { CaptureWarning, Screenshot } from "@/types"

type Details = Record<string, unknown>

/** One item a warning lists: a broken asset, a hidden overlay… */
interface Sample {
  text: string
  /** Only for http(s) URLs. */
  href?: string
  /** Short qualifiers shown first: HTTP status, category, rule. */
  tags: string[]
  /** The full URL, reason or position, on hover. */
  hint?: string
}

interface DetailsView {
  lines: ReactNode[]
  samples: Sample[]
}

const EMPTY_VIEW: DetailsView = { lines: [], samples: [] }

const UNSTABLE_REASONS = new Map([
  ["dom", "the DOM kept changing"],
  ["fonts", "web fonts were still loading"],
])

/**
 * The capture warnings of a screenshot — each in an amber alert with a short
 * explanation and its details (broken assets, missing stylesheets, overflow…)
 * — followed by the informational notes, collapsed. Nothing for a clean
 * capture.
 */
export function WarningsList({
  screenshot,
  className,
}: {
  screenshot: Screenshot
  className?: string
}) {
  const { warnings, notes } = getScreenshotWarnings(screenshot)
  if (warnings.length === 0 && notes.length === 0) return null

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {warnings.map((warning, index) => (
        <WarningAlert key={`${warning.code}-${index}`} title={<EntryTitle warning={warning} />}>
          <EntryBody warning={warning} />
        </WarningAlert>
      ))}

      {notes.length > 0 && (
        <Collapsible className="rounded-lg border bg-card text-sm">
          <CollapsibleTrigger className="group flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <Info className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="shrink-0 font-medium">
              {notes.length} capture {notes.length === 1 ? "note" : "notes"}
            </span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {summarizeWarnings(notes)}
            </span>
            <ChevronDown
              className="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
              aria-hidden="true"
            />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul className="flex flex-col gap-3 border-t px-2.5 py-2.5 text-muted-foreground">
              {notes.map((note, index) => (
                <li key={`${note.code}-${index}`} className="flex min-w-0 flex-col gap-0.5">
                  <span className="font-medium text-foreground">
                    <EntryTitle warning={note} />
                  </span>
                  <EntryBody warning={note} />
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}

function EntryTitle({ warning }: { warning: CaptureWarning }) {
  const count = warningCount(warning)
  return (
    <>
      {warningTitle(warning)}
      {count > 1 && <span className="ml-1.5 font-normal tabular-nums opacity-75">×{count}</span>}
    </>
  )
}

function EntryBody({ warning }: { warning: CaptureWarning }) {
  const description = warningDescription(warning)
  // Known codes show this frontend's explanation; the capture's own message
  // (often more specific) stays available on hover.
  const hint = isKnownWarningCode(warning.code) && warning.message ? warning.message : undefined
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {description && <div title={hint}>{description}</div>}
      <WarningDetails warning={warning} />
    </div>
  )
}

function WarningDetails({ warning }: { warning: CaptureWarning }) {
  const details = isRecord(warning.details) ? warning.details : {}
  const { lines, samples } = specificDetails(warning.code, details) ?? genericDetails(details)
  if (lines.length === 0 && samples.length === 0) return null
  const more = warningCount(warning) - samples.length

  return (
    <div className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
      {lines.map((line, index) => (
        <div key={index} className="break-words">
          {line}
        </div>
      ))}
      {samples.length > 0 && (
        <ul className="flex min-w-0 flex-col gap-0.5">
          {samples.map((sample, index) => (
            <SampleRow key={index} sample={sample} />
          ))}
          {more > 0 && <li>+{more} more</li>}
        </ul>
      )}
    </div>
  )
}

function SampleRow({ sample }: { sample: Sample }) {
  const textClass = "min-w-0 truncate font-mono text-foreground/80"
  return (
    <li className="flex min-w-0 items-baseline gap-1.5" title={sample.hint}>
      {sample.tags.length > 0 && (
        <span className="max-w-[50%] shrink-0 truncate tabular-nums">{sample.tags.join(" · ")}</span>
      )}
      {sample.href ? (
        <a
          href={sample.href}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(textClass, "underline-offset-2 hover:underline")}
        >
          {sample.text}
        </a>
      ) : (
        <span className={textClass}>{sample.text}</span>
      )}
    </li>
  )
}

function UrlLine({ url }: { url: string }) {
  const href = safeHref(url)
  const urlClass = "break-all font-mono text-foreground/80"
  return (
    <>
      To{" "}
      {href ? (
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
      )}
    </>
  )
}

// ─── Details by code ─────────────────────────────────────────────────────────

/** What a known code's details say, or null when they don't have the expected
 *  shape (the generic view then lists whatever is there). */
function specificDetails(code: string, details: Details): DetailsView | null {
  switch (code) {
    case "http_error":
      // The title already names the status ("HTTP 404").
      return asNumber(details.status) !== undefined ? EMPTY_VIEW : null

    case "redirected": {
      const finalUrl = asString(details.final_url)
      return finalUrl ? { lines: [<UrlLine url={finalUrl} />], samples: [] } : null
    }

    case "fonts_timeout": {
      const families = asArray(details.families)
        .map((family) =>
          isRecord(family) ? (asString(family.family) ?? asString(family.name)) : asString(family),
        )
        .filter((family): family is string => !!family)
      const timeoutMs = asNumber(details.timeout_ms)
      const waited = timeoutMs !== undefined ? `waited ${formatMs(timeoutMs)}` : ""
      if (families.length === 0) return waited ? textView(capitalize(waited)) : null
      return textView([families.join(", "), waited].filter(Boolean).join(" · "))
    }

    case "dom_unstable": {
      const reason = asString(details.reason)
      if (!reason) return null
      return textView(`Reason: ${UNSTABLE_REASONS.get(reason) ?? humanize(reason).toLowerCase()}`)
    }

    case "overflow_cropped": {
      const pageWidth = asNumber(details.document_width)
      const frameWidth = asNumber(details.clipped_width) ?? asNumber(details.viewport_width)
      const parts = [
        pageWidth !== undefined ? `page ${pageWidth} px wide` : "",
        frameWidth !== undefined ? `frame ${frameWidth} px` : "",
      ].filter(Boolean)
      return parts.length > 0 ? textView(capitalize(parts.join(", "))) : null
    }

    case "assets_missing":
    case "assets_pending":
    case "assets_recovered": {
      const samples = assetSamples(details)
      return samples.length > 0 ? { lines: [], samples } : null
    }

    case "styles_missing": {
      const samples = assetSamples(details)
      const reasons = unattributedReasons(details)
      if (samples.length === 0 && reasons.length === 0) return null
      return { lines: reasons.length > 0 ? [`Reason: ${reasons.join("; ")}`] : [], samples }
    }

    case "overlays_hidden": {
      const samples = asArray(details.samples).filter(isRecord).map(overlaySample)
      return samples.length > 0 ? { lines: [], samples } : null
    }

    default:
      return null
  }
}

function textView(text: string): DetailsView {
  return { lines: [text], samples: [] }
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Broken assets: samples [{kind, url, status, category, reason}], or a plain
 *  `urls` list with `reasons` given per URL (a parallel list or a map). */
function assetSamples(details: Details): Sample[] {
  const records = asArray(details.samples).filter(isRecord)
  if (records.length > 0) {
    return records.map((record) => {
      const url = asString(record.url) ?? asString(record.src) ?? ""
      const reason = asString(record.reason)
      return {
        text: url ? shortUrl(url) : (reason ?? "unknown asset"),
        href: safeHref(url),
        tags: [scalarText(record.status), asString(record.category), asString(record.kind)].filter(
          (tag): tag is string => !!tag,
        ),
        hint: [url, reason].filter(Boolean).join("\n") || undefined,
      }
    })
  }

  const urls = asStrings(details.urls)
  const reasons = details.reasons
  return urls.map((url, index) => {
    const reason = Array.isArray(reasons)
      ? reasons.length === urls.length
        ? asString(reasons[index])
        : undefined
      : isRecord(reasons)
        ? asString(reasons[url])
        : undefined
    return {
      text: shortUrl(url),
      href: safeHref(url),
      tags: reason && reason.length <= 32 ? [reason] : [],
      hint: [url, reason].filter(Boolean).join("\n"),
    }
  })
}

/** Reasons that can't be matched to a URL (a list of a different length, or a
 *  single string). */
function unattributedReasons(details: Details): string[] {
  const reasons = details.reasons
  if (isRecord(reasons)) return []
  if (Array.isArray(reasons) && reasons.length === asStrings(details.urls).length) return []
  return asStrings(reasons)
}

/** Hidden overlay: {tier, rule, element: "tag#id.class", rect}. */
function overlaySample(record: Details): Sample {
  const element = asString(record.element)
  const rule = asString(record.rule)
  const tier = scalarText(record.tier)
  return {
    text: element ?? rule ?? "element",
    tags: element && rule ? [rule] : [],
    hint:
      [element, tier && `tier ${tier}`, rule && `rule ${rule}`, rectText(record.rect)]
        .filter(Boolean)
        .join(" · ") || undefined,
  }
}

function rectText(rect: unknown): string | undefined {
  const [width, height] = isRecord(rect)
    ? [asNumber(rect.width) ?? asNumber(rect.w), asNumber(rect.height) ?? asNumber(rect.h)]
    : Array.isArray(rect) && rect.length === 4
      ? [asNumber(rect[2]), asNumber(rect[3])]
      : [undefined, undefined]
  return width !== undefined && height !== undefined
    ? `${Math.round(width)}×${Math.round(height)} px`
    : undefined
}

// ─── Generic details (codes without a dedicated view) ────────────────────────

const RESERVED_KEYS = new Set(["count", "samples"])
const PRIMARY_KEYS = ["url", "src", "href", "element", "selector", "name", "family", "library"]

/** Every detail as "Key: value" (`*_ms` as a duration), and samples by their
 *  most telling field. */
function genericDetails(details: Details): DetailsView {
  const lines = Object.entries(details)
    .filter(([key]) => !RESERVED_KEYS.has(key))
    .map(([key, value]) => fact(key, value))
    .filter((line): line is string => line !== null)
  const samples = asArray(details.samples)
    .map(genericSample)
    .filter((sample): sample is Sample => sample !== null)
  return { lines, samples }
}

function fact(key: string, value: unknown): string | null {
  const isMs = key.endsWith("_ms")
  const label = humanize(isMs ? key.slice(0, -3) : key)
  if (typeof value === "boolean") return value ? label : null
  const ms = isMs ? asNumber(value) : undefined
  if (ms !== undefined) return `${label}: ${formatMs(ms)}`
  const text = Array.isArray(value)
    ? listText(value.map(scalarText))
    : isRecord(value)
      ? listText(Object.entries(value).map(([k, v]) => withKey(k, scalarText(v))))
      : scalarText(value)
  return text ? `${label}: ${text}` : null
}

function genericSample(item: unknown): Sample | null {
  const scalar = scalarText(item)
  if (scalar) return { text: scalar, tags: [] }
  if (!isRecord(item)) return null
  const primaryKey = PRIMARY_KEYS.find((key) => asString(item[key]) !== undefined)
  const primary = primaryKey ? (asString(item[primaryKey]) ?? "") : ""
  const others = Object.entries(item)
    .filter(([key]) => key !== primaryKey)
    .map(([key, value]) => withKey(key, scalarText(value)))
    .filter((text): text is string => !!text)
  if (!primary && others.length === 0) return null
  const isUrl = primaryKey === "url" || primaryKey === "src" || primaryKey === "href"
  return {
    text: primary ? (isUrl ? shortUrl(primary) : primary) : others.join(", "),
    href: isUrl ? safeHref(primary) : undefined,
    tags: [],
    hint: [primary, ...others].filter(Boolean).join("\n") || undefined,
  }
}

/** Numbers and strings as text ("second_chance" → "second chance"). */
function scalarText(value: unknown): string | undefined {
  const number = asNumber(value)
  if (number !== undefined) return String(Number.isInteger(number) ? number : Math.round(number * 100) / 100)
  const text = asString(value)
  if (text === undefined) return undefined
  return /^[a-z][a-z0-9_]*$/.test(text) ? text.replace(/_/g, " ") : text
}

function withKey(key: string, text: string | undefined): string | undefined {
  return text === undefined ? undefined : `${key.replace(/_/g, " ")} ${text}`
}

function listText(items: (string | undefined)[]): string | undefined {
  const texts = items.filter((item): item is string => !!item)
  return texts.length > 0 ? texts.join(", ") : undefined
}
