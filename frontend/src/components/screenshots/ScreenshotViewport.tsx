import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react"
import type { ReactNode, WheelEvent, MouseEvent as ReactMouseEvent } from "react"
import QuickPinchZoom, {
  make3dTransformValue,
  type UpdateAction,
} from "react-quick-pinch-zoom"
import {
  AlertTriangle,
  ImageOff,
  Maximize2,
  Minus,
  Plus,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ButtonGroup } from "@/components/ui/button-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { StateMessage } from "@/components/ui/StateMessage"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Screenshot } from "@/types"

const MIN_ZOOM = 1
const MAX_ZOOM = 4
const DOUBLE_CLICK_ZOOM = 2
const BUTTON_ZOOM_STEP = 0.2
const WHEEL_ZOOM_STEP = 0.15
const ZOOM_EPSILON = 0.01

type QuickPinchZoomInstance = InstanceType<typeof QuickPinchZoom>

export interface ScreenshotViewportHandle {
  fitToScreen: (animated?: boolean) => void
  reset: (animated?: boolean) => void
  zoomIn: () => void
  zoomOut: () => void
}

interface ScreenshotViewportProps {
  screenshot: Screenshot
  imgUrl: string
  isRefreshingSignedUrl: boolean
  onRequestSignedUrlRefresh: () => Promise<boolean>
  overlay?: ReactNode
  className?: string
}

function clampZoomFactor(value: number): number {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value))
}

function isZoomed(zoomFactor: number): boolean {
  return zoomFactor > MIN_ZOOM + ZOOM_EPSILON
}

export const ScreenshotViewport = forwardRef<
  ScreenshotViewportHandle,
  ScreenshotViewportProps
>(function ScreenshotViewport(
  {
    screenshot,
    imgUrl,
    isRefreshingSignedUrl,
    onRequestSignedUrlRefresh,
    overlay,
    className,
  },
  ref,
) {
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const pinchZoomRef = useRef<QuickPinchZoomInstance | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)

  const [transform, setTransform] = useState<UpdateAction>({
    x: 0,
    y: 0,
    scale: 1,
  })
  const [baseScale, setBaseScale] = useState<number | null>(null)
  const [isPanning, setIsPanning] = useState(false)
  const [loadedImageKey, setLoadedImageKey] = useState<string | null>(null)
  const [failedImageKey, setFailedImageKey] = useState<string | null>(null)

  const imageKey = `${screenshot.id}:${screenshot.signed_url}`
  const imageLoaded = loadedImageKey === imageKey
  const imageFailed = failedImageKey === imageKey
  const imageVisible = imageLoaded && baseScale !== null

  const zoomFactor = useMemo(() => {
    if (!baseScale || baseScale <= 0) return MIN_ZOOM
    return clampZoomFactor(transform.scale / baseScale)
  }, [baseScale, transform.scale])

  const canPan = imageVisible

  const getViewportPoint = useCallback((clientX: number, clientY: number) => {
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return null
    return {
      x: clientX - rect.left,
      y: clientY - rect.top,
    }
  }, [])

  const getViewportCenter = useCallback(() => {
    const element = viewportRef.current
    if (!element) return null
    return {
      x: element.clientWidth / 2,
      y: element.clientHeight / 2,
    }
  }, [])

  const resetToBase = useCallback((animated = true) => {
    const pinchZoom = pinchZoomRef.current
    const content = contentRef.current
    if (!pinchZoom || !content) return

    pinchZoom.alignCenter({
      x: content.offsetWidth / 2,
      y: content.offsetHeight / 2,
      scale: MIN_ZOOM,
      animated,
    })
  }, [])

  const scaleToPoint = useCallback(
    (point: { x: number; y: number }, nextZoomFactor: number, animated = true) => {
      pinchZoomRef.current?.scaleTo({
        x: point.x,
        y: point.y,
        scale: clampZoomFactor(nextZoomFactor),
        animated,
      })
    },
    [],
  )

  const zoomByStep = useCallback(
    (delta: number, animated = true) => {
      const point = getViewportCenter()
      if (!point) return
      scaleToPoint(point, zoomFactor + delta, animated)
    },
    [getViewportCenter, scaleToPoint, zoomFactor],
  )

  const handleUpdate = useCallback((nextTransform: UpdateAction) => {
    if (nextTransform.scale > 0) {
      setBaseScale((current) => current ?? nextTransform.scale)
    }
    setTransform(nextTransform)
  }, [])

  const handleWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (screenshot.status !== "ok" || !screenshot.signed_url || imageFailed) return

      event.preventDefault()
      const point = getViewportPoint(event.clientX, event.clientY)
      if (!point) return

      const direction = event.deltaY < 0 ? 1 : -1
      scaleToPoint(point, zoomFactor + direction * WHEEL_ZOOM_STEP, false)
    },
    [
      getViewportPoint,
      imageFailed,
      scaleToPoint,
      screenshot.signed_url,
      screenshot.status,
      zoomFactor,
    ],
  )

  const handleDoubleClick = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      if (screenshot.status !== "ok" || !screenshot.signed_url || imageFailed) return

      event.preventDefault()
      if (isZoomed(zoomFactor)) {
        resetToBase(true)
        return
      }

      const point = getViewportPoint(event.clientX, event.clientY)
      if (!point) return
      scaleToPoint(point, DOUBLE_CLICK_ZOOM, true)
    },
    [
      getViewportPoint,
      imageFailed,
      resetToBase,
      scaleToPoint,
      screenshot.signed_url,
      screenshot.status,
      zoomFactor,
    ],
  )

  const handleImageError = useCallback(async () => {
    const refreshed = await onRequestSignedUrlRefresh()
    if (!refreshed) setFailedImageKey(imageKey)
  }, [imageKey, onRequestSignedUrlRefresh])

  useImperativeHandle(
    ref,
    () => ({
      fitToScreen: (animated = true) => resetToBase(animated),
      reset: (animated = true) => resetToBase(animated),
      zoomIn: () => zoomByStep(BUTTON_ZOOM_STEP),
      zoomOut: () => zoomByStep(-BUTTON_ZOOM_STEP),
    }),
    [resetToBase, zoomByStep],
  )

  if (screenshot.status !== "ok") {
    return (
      <StateMessage
        tone="destructive"
        icon={<AlertTriangle />}
        title="Screenshot failed"
        description={screenshot.error_message ?? undefined}
        className="min-h-[420px] border-solid bg-card"
      />
    )
  }

  if (!screenshot.signed_url) {
    return (
      <StateMessage
        icon={<ImageOff />}
        title="Not available"
        className="min-h-[420px] border-solid bg-card"
      />
    )
  }

  return (
    <div className={cn("overflow-hidden rounded-xl border bg-card", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <span className="text-xs tabular-nums text-muted-foreground">
          Zoom {Math.round(zoomFactor * 100)}%
        </span>
        <ButtonGroup aria-label="Zoom">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label="Zoom out"
                onClick={() => zoomByStep(-BUTTON_ZOOM_STEP)}
              >
                <Minus />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Zoom out</TooltipContent>
          </Tooltip>
          <Button variant="outline" size="sm" onClick={() => resetToBase(true)}>
            <Maximize2 data-icon="inline-start" />
            Fit
          </Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon-sm"
                aria-label="Zoom in"
                onClick={() => zoomByStep(BUTTON_ZOOM_STEP)}
              >
                <Plus />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Zoom in</TooltipContent>
          </Tooltip>
        </ButtonGroup>
      </div>

      {screenshot.captured_with_unstable_dom && (
        <Alert className="rounded-none border-0 border-b bg-warning/10 text-warning">
          <AlertTriangle />
          <AlertDescription className="text-foreground/80">
            {screenshot.error_message ??
              "Captured with unstable DOM. Content may still have been settling."}
          </AlertDescription>
        </Alert>
      )}

      <div
        ref={viewportRef}
        className={cn(
          "relative h-[calc(100vh-300px)] min-h-[420px] overflow-hidden bg-muted/20",
          canPan ? (isPanning ? "cursor-grabbing" : "cursor-grab") : "cursor-zoom-in",
        )}
        onWheel={handleWheel}
        onDoubleClick={handleDoubleClick}
      >
        {!imageVisible && !imageFailed && !isRefreshingSignedUrl && (
          <Skeleton className="absolute inset-0 rounded-none" />
        )}

        {imageFailed && !isRefreshingSignedUrl ? (
          <StateMessage
            tone="destructive"
            icon={<ImageOff />}
            title="Could not load the image"
            description="The secure link was refreshed, but the image still failed to load."
            className="h-full rounded-none border-0"
          />
        ) : (
          <QuickPinchZoom
            ref={pinchZoomRef}
            onUpdate={handleUpdate}
            minZoom={MIN_ZOOM}
            maxZoom={MAX_ZOOM}
            inertia={false}
            enforceBoundsDuringZoom
            draggableUnZoomed={true}
            tapZoomFactor={0}
            doubleTapToggleZoom={false}
            shouldInterceptWheel={() => false}
            onDragStart={() => setIsPanning(true)}
            onDragEnd={() => setIsPanning(false)}
            containerProps={{
              className: "absolute inset-0 touch-none select-none",
            }}
          >
            <div
              ref={contentRef}
              className="relative w-full"
              style={{
                transform: make3dTransformValue(transform),
                willChange: "transform",
              }}
            >
              <img
                src={imgUrl}
                alt={`${screenshot.url} @ ${screenshot.width}x${screenshot.height}`}
                loading="lazy"
                onLoad={() => {
                  setLoadedImageKey(imageKey)
                  setFailedImageKey(null)
                }}
                onError={handleImageError}
                className="block h-auto max-w-full pointer-events-none"
                style={{
                  opacity: imageVisible ? 1 : 0,
                  transition: "opacity 180ms ease",
                }}
              />

              {overlay ? (
                <div className="pointer-events-none absolute inset-0">{overlay}</div>
              ) : null}
            </div>
          </QuickPinchZoom>
        )}

        {isRefreshingSignedUrl && (
          <Badge variant="secondary" className="absolute right-3 bottom-3 gap-1.5">
            <Spinner className="size-3" />
            Refreshing secure URL…
          </Badge>
        )}
      </div>
    </div>
  )
})
