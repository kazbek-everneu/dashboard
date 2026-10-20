import { useRef, useState } from "react"
import {
  Copy,
  Check,
  Download,
  Monitor,
  Tablet,
  Smartphone,
  AlertTriangle,
  RotateCcw,
  Loader2,
} from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { extractApiError, getDeviceType } from "@/lib/helpers"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { screenshotsApi } from "@/api/screenshots"
import { buildImgUrl, getRenderedStripDimensions } from "./screenshot-utils"
import type { Screenshot } from "@/types"

const deviceIcons = {
  desktop: Monitor,
  tablet: Tablet,
  mobile: Smartphone,
}

interface ScreenshotCardProps {
  screenshot: Screenshot
  onClick: () => void
  variant?: "grid" | "strip"
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: () => void
}

function ScreenshotCardFooter({ screenshot: s }: { screenshot: Screenshot }) {
  const imgUrl = buildImgUrl(s)
  const downloadFilename = [
    s.url?.split("/").filter(Boolean).pop() || "screenshot",
    `${s.width}x${s.height}`,
    s.browser,
  ].join("_") + `.${s.file_format === "jpeg" ? "jpg" : s.file_format}`
  const browserLabel =
    s.browser === "webkit"
      ? "Safari (WebKit)"
      : s.browser.charAt(0).toUpperCase() + s.browser.slice(1)

  const deviceType = getDeviceType(s.width)
  const DeviceIcon = deviceIcons[deviceType]
  const deviceTitle = deviceType.charAt(0).toUpperCase() + deviceType.slice(1)
  const [copied, setCopied] = useState(false)

  const queryClient = useQueryClient()
  const recaptureMutation = useMutation({
    mutationFn: () => screenshotsApi.recapture(s.id),
    onSuccess: (updated) => {
      queryClient.setQueryData<Screenshot[]>(["screenshots", s.task_id], (prev) =>
        prev ? prev.map((item) => (item.id === updated.id ? updated : item)) : prev,
      )
      void queryClient.invalidateQueries({ queryKey: ["screenshots", s.task_id] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })
  const isRecapturing = s.status === "recapturing" || recaptureMutation.isPending

  async function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    await navigator.clipboard.writeText(imgUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  function handleRecapture(e: React.MouseEvent) {
    e.stopPropagation()
    recaptureMutation.mutate()
  }

  return (
    <div className="flex min-w-0 items-center justify-between gap-3 border-t surface-divider px-3 py-2.5">
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
        <span title={deviceTitle} className="inline-flex shrink-0">
          <DeviceIcon size={12} className="text-muted-foreground" />
        </span>
        <span title={browserLabel}>
          <BrowserIcon browser={s.browser} size={13} />
        </span>
        <span
          className="truncate text-xs font-mono leading-none text-muted-foreground"
          title={`${s.width} x ${s.height}`}
        >
          {s.width}x{s.height}
        </span>
        <span
          className={cn(
            "inline-block h-1.5 w-1.5 shrink-0",
            s.status === "failed"
              ? "bg-red-500"
              : s.captured_with_unstable_dom
                ? "bg-amber-500"
                : "bg-green-500",
          )}
          title={
            s.status === "failed"
              ? "Failed"
              : s.captured_with_unstable_dom
                ? "Captured with unstable DOM"
                : "OK"
          }
        />
        {s.captured_with_unstable_dom && (
          <span
            title={s.error_message ?? "Captured with unstable DOM"}
            className="inline-flex shrink-0"
          >
            <AlertTriangle size={12} className="text-amber-500" />
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          onClick={handleRecapture}
          disabled={isRecapturing}
          title={isRecapturing ? "Re-capturing…" : "Re-capture this screenshot"}
          className="p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isRecapturing ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <RotateCcw size={12} />
          )}
        </button>
        {s.signed_url && s.status === "ok" && (
          <>
            <button
              type="button"
              onClick={handleCopy}
              title="Copy URL"
              className="p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
            <a
              href={imgUrl}
              download={downloadFilename}
              onClick={(e) => e.stopPropagation()}
              title="Download"
              className="p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Download size={12} />
            </a>
          </>
        )}
      </div>
    </div>
  )
}

export function ScreenshotCard({
  screenshot: s,
  onClick,
  variant = "grid",
  selectable = false,
  selected = false,
  onToggleSelect,
}: ScreenshotCardProps) {
  const imgUrl = buildImgUrl(s)
  const imgAlt = `${s.url} @ ${s.width}x${s.height} (${s.browser})`
  const SCROLL_SPEED = 300 // px per second
  const imgRef = useRef<HTMLImageElement>(null)
  const containerRef = useRef<HTMLButtonElement>(null)
  const [duration, setDuration] = useState(8)
  const [loadedFullImage, setLoadedFullImage] = useState(false)
  const stripDimensions = getRenderedStripDimensions(s)
  const isStrip = variant === "strip"

  function handleImageLoad() {
    setLoadedFullImage(true)
    if (isStrip) return

    const img = imgRef.current
    const container = containerRef.current
    if (!img || !container) return
    const scrollDistance = Math.max(0, img.offsetHeight - container.offsetHeight)
    const calculated = scrollDistance / SCROLL_SPEED
    setDuration(Math.max(2, Math.min(20, calculated)))
  }

  return (
    <div
      className={cn(
        "glass-card hover-lift overflow-hidden",
        isStrip && "strip-card",
        s.status === "failed" && "border-destructive/50",
        selected && "ring-2 ring-foreground/50",
      )}
      style={isStrip ? { width: `${stripDimensions.width}px` } : undefined}
    >
      <div className="relative">
        {s.status === "recapturing" ? (
          <div
            className={cn(
              "relative flex items-center justify-center overflow-hidden bg-muted/20",
              isStrip ? "strip-preview-frame" : "h-[220px] w-full",
            )}
            style={isStrip ? { height: `${stripDimensions.height}px` } : undefined}
          >
            {s.image_width && s.signed_url && (
              <img src={imgUrl} alt={imgAlt} className="h-full w-full object-cover opacity-30" />
            )}
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-background/40">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground">Recapturing…</span>
            </div>
          </div>
        ) : s.status === "ok" ? (
          <button
            ref={isStrip ? undefined : containerRef}
            type="button"
            className={cn(
              "relative cursor-pointer bg-muted/20",
              isStrip ? "strip-preview-frame" : "scroll-preview-container",
            )}
            style={
              isStrip
                ? { height: `${stripDimensions.height}px` }
                : ({ "--scroll-duration": `${duration}s` } as React.CSSProperties)
            }
            onClick={onClick}
          >
            {!loadedFullImage && <div className="screenshot-progressive-preview" aria-hidden="true" />}
            <img
              ref={imgRef}
              src={imgUrl}
              alt={imgAlt}
              loading="lazy"
              width={s.image_width ?? s.width}
              height={s.image_height ?? s.height}
              onLoad={handleImageLoad}
              className={cn(
                "screenshot-progressive-image",
                loadedFullImage && "is-loaded",
                isStrip && "block h-full w-full object-contain",
              )}
            />
          </button>
        ) : isStrip ? (
          <button
            type="button"
            className="strip-preview-frame flex cursor-pointer flex-col items-center justify-center gap-1.5 bg-destructive/5 px-3 text-center"
            style={{ height: `${stripDimensions.height}px` }}
            onClick={onClick}
          >
            <AlertTriangle className="h-5 w-5 shrink-0 text-destructive" />
            <span className="text-xs font-semibold text-destructive">Failed</span>
            <span
              className="line-clamp-2 text-[11px] leading-4 text-destructive/80"
              title={s.error_message ?? undefined}
            >
              {s.error_message ?? "Screenshot capture failed"}
            </span>
          </button>
        ) : (
          <button
            type="button"
            className="flex h-[220px] w-full cursor-pointer flex-col items-center justify-center gap-2 bg-destructive/5 px-4 text-center"
            onClick={onClick}
          >
            <AlertTriangle className="h-6 w-6 shrink-0 text-destructive" />
            <span className="text-sm font-semibold text-destructive">Failed</span>
            <span
              className="line-clamp-3 text-xs leading-4 text-destructive/80"
              title={s.error_message ?? undefined}
            >
              {s.error_message ?? "Screenshot capture failed"}
            </span>
          </button>
        )}

        {selectable && onToggleSelect && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              onToggleSelect()
            }}
            className={cn(
              "absolute right-2 top-2 z-10 border px-2 py-1 text-[11px] font-medium",
              selected
                ? "bg-foreground text-background"
                : "bg-background/90 text-foreground hover:bg-background",
            )}
          >
            {selected ? "Selected" : "Select"}
          </button>
        )}
      </div>
      <ScreenshotCardFooter screenshot={s} />
    </div>
  )
}
