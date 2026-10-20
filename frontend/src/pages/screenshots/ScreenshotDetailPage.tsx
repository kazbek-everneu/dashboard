import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft,
  Download,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  Loader2,
} from "lucide-react"
import { screenshotsApi } from "@/api/screenshots"
import { buildImgUrl } from "@/components/screenshots/screenshot-utils"
import { ScreenshotViewport } from "@/components/screenshots/ScreenshotViewport"
import PageHeader from "@/components/layout/PageHeader"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { Button } from "@/components/ui/button"
import { MetadataCard } from "@/components/ui/MetadataCard"
import { StateMessage } from "@/components/ui/StateMessage"
import { formatDate, shortId, extractApiError } from "@/lib/helpers"
import type { Screenshot } from "@/types"

function browserLabel(browser: string): string {
  if (browser === "msedge") return "Edge"
  if (browser === "webkit") return "Safari (WebKit)"
  return browser.charAt(0).toUpperCase() + browser.slice(1)
}

function getPathnameLabel(url: string): string {
  try {
    const pathname = new URL(url).pathname
    return pathname || "/"
  } catch {
    return url
  }
}

function formatViewportBrowser(screenshot: Screenshot): string {
  return `${screenshot.width}x${screenshot.height} / ${browserLabel(screenshot.browser)}`
}

function findNextIndex(
  screenshots: Screenshot[],
  currentIndex: number,
  predicate: (candidate: Screenshot, current: Screenshot) => boolean,
): number | null {
  if (currentIndex < 0 || screenshots.length < 2) return null
  const current = screenshots[currentIndex]

  for (let step = 1; step < screenshots.length; step += 1) {
    const index = (currentIndex + step) % screenshots.length
    if (predicate(screenshots[index], current)) return index
  }

  return null
}

export function ScreenshotDetailPage() {
  const { taskId, screenshotId } = useParams<{ taskId: string; screenshotId: string }>()
  const navigate = useNavigate()
  const refreshAttemptsRef = useRef<Record<string, number>>({})
  const [isRefreshingSignedUrl, setIsRefreshingSignedUrl] = useState(false)

  const queryClient = useQueryClient()
  const { data: screenshots = [], isLoading, refetch } = useQuery({
    queryKey: ["screenshots", taskId],
    queryFn: () => screenshotsApi.list(taskId!),
    enabled: !!taskId,
    // Poll while the screenshot being viewed is re-capturing.
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.id === screenshotId && s.status === "recapturing")
        ? 3000
        : false,
  })

  const recaptureMutation = useMutation({
    mutationFn: () => screenshotsApi.recapture(screenshotId!),
    onSuccess: (updated) => {
      queryClient.setQueryData<Screenshot[]>(["screenshots", taskId], (prev) =>
        prev ? prev.map((item) => (item.id === updated.id ? updated : item)) : prev,
      )
      void queryClient.invalidateQueries({ queryKey: ["screenshots", taskId] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  const currentIndex = screenshots.findIndex((s: Screenshot) => s.id === screenshotId)
  const screenshot: Screenshot | undefined = screenshots[currentIndex]

  const prevIndex = currentIndex > 0 ? currentIndex - 1 : null
  const nextIndex = currentIndex >= 0 && currentIndex < screenshots.length - 1 ? currentIndex + 1 : null

  const nextPageIndex = useMemo(
    () => findNextIndex(screenshots, currentIndex, (candidate, current) => candidate.url !== current.url),
    [screenshots, currentIndex],
  )

  const nextViewportBrowserIndex = useMemo(() => {
    const samePageNext = findNextIndex(
      screenshots,
      currentIndex,
      (candidate, current) =>
        candidate.url === current.url &&
        (candidate.width !== current.width ||
          candidate.height !== current.height ||
          candidate.browser !== current.browser),
    )
    if (samePageNext !== null) return samePageNext

    return findNextIndex(
      screenshots,
      currentIndex,
      (candidate, current) =>
        candidate.width !== current.width ||
        candidate.height !== current.height ||
        candidate.browser !== current.browser,
    )
  }, [screenshots, currentIndex])

  const navigateToIndex = useCallback(
    (index: number | null) => {
      if (index === null) return
      if (!taskId) return
      const target = screenshots[index]
      if (!target) return
      navigate(`/tasks/${taskId}/screenshots/${target.id}`)
    },
    [navigate, screenshots, taskId],
  )

  const requestSignedUrlRefresh = useCallback(async (): Promise<boolean> => {
    if (!screenshot?.id) return false
    const attempts = refreshAttemptsRef.current[screenshot.id] ?? 0
    if (attempts >= 1) return false

    refreshAttemptsRef.current[screenshot.id] = attempts + 1
    setIsRefreshingSignedUrl(true)
    try {
      const result = await refetch()
      return !result.error
    } catch {
      return false
    } finally {
      setIsRefreshingSignedUrl(false)
    }
  }, [refetch, screenshot?.id])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "ArrowLeft") {
        event.preventDefault()
        navigateToIndex(prevIndex)
      } else if (event.key === "ArrowRight") {
        event.preventDefault()
        navigateToIndex(nextIndex)
      } else if (event.key === "]") {
        event.preventDefault()
        navigateToIndex(nextPageIndex)
      } else if (event.key === "[") {
        event.preventDefault()
        navigateToIndex(nextViewportBrowserIndex)
      }
    }

    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [navigateToIndex, nextIndex, nextPageIndex, nextViewportBrowserIndex, prevIndex])

  if (isLoading) {
    return (
      <StateMessage compact title="Loading screenshot" description="Fetching screenshot details..." />
    )
  }

  if (!screenshot) {
    return <StateMessage compact tone="destructive" title="Screenshot not found" />
  }

  const imgUrl = buildImgUrl(screenshot)
  const isRecapturing = screenshot.status === "recapturing" || recaptureMutation.isPending
  const downloadFilename =
    [
      screenshot.url?.split("/").filter(Boolean).pop() || "screenshot",
      `${screenshot.width}x${screenshot.height}`,
      screenshot.browser,
    ].join("_") + `.${screenshot.file_format === "jpeg" ? "jpg" : screenshot.file_format}`

  return (
    <div className="page-shell">
      <PageHeader
        title={
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => navigate(`/tasks/${taskId}`)}
              className="h-auto p-1.5"
            >
              <ArrowLeft className="mr-1.5 h-4 w-4" />
              Task #{shortId(taskId!)}
            </Button>
          </div>
        }
        description={screenshot.url}
        meta={`Capture ${currentIndex + 1} of ${screenshots.length}`}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => recaptureMutation.mutate()}
              disabled={isRecapturing}
              title={isRecapturing ? "Re-capturing…" : "Re-capture this screenshot"}
            >
              {isRecapturing ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="mr-1.5 h-4 w-4" />
              )}
              {isRecapturing ? "Recapturing…" : "Redo"}
            </Button>
            {screenshot.status === "ok" && screenshot.signed_url && (
              <a
                href={imgUrl}
                download={downloadFilename}
                className="inline-flex items-center gap-1.5 border bg-background px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
              >
                <Download className="h-4 w-4" />
                Download
              </a>
            )}
          </div>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
        <div className="space-y-4">
          <ScreenshotViewport
            key={screenshot.id}
            screenshot={screenshot}
            imgUrl={imgUrl}
            isRefreshingSignedUrl={isRefreshingSignedUrl}
            onRequestSignedUrlRefresh={requestSignedUrlRefresh}
          />

          {screenshots.length > 1 && (
            <div className="glass-card flex items-center justify-between px-4 py-3">
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigateToIndex(prevIndex)}
                disabled={prevIndex === null}
              >
                <ChevronLeft className="mr-1 h-4 w-4" />
                Prev
              </Button>

              <span className="text-sm text-muted-foreground">
                {currentIndex + 1} / {screenshots.length}
              </span>

              <Button
                variant="outline"
                size="sm"
                onClick={() => navigateToIndex(nextIndex)}
                disabled={nextIndex === null}
              >
                Next
                <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          )}
        </div>

        <aside className="space-y-4">
          {screenshot.status === "failed" && (
            <MetadataCard label="Error">
              <p className="break-words text-sm leading-5 text-destructive">
                {screenshot.error_message ?? "Screenshot capture failed"}
              </p>
            </MetadataCard>
          )}

          {screenshot.status === "ok" &&
            screenshot.captured_with_unstable_dom &&
            screenshot.error_message && (
              <MetadataCard label="Warning">
                <p className="break-words text-sm leading-5 text-amber-600 dark:text-amber-400">
                  {screenshot.error_message}
                </p>
              </MetadataCard>
            )}

          <MetadataCard label="Navigation">
            <div className="space-y-2">
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => navigateToIndex(nextIndex)}
                disabled={nextIndex === null}
              >
                Next Screenshot
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => navigateToIndex(nextPageIndex)}
                disabled={nextPageIndex === null}
              >
                Next Page
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-start"
                onClick={() => navigateToIndex(nextViewportBrowserIndex)}
                disabled={nextViewportBrowserIndex === null}
              >
                Next Viewport/Browser
              </Button>
              <p className="text-xs text-muted-foreground">
                Shortcuts: Left Arrow, Right Arrow, ], [
              </p>
            </div>
          </MetadataCard>

          <MetadataCard label="Capture">
            <div className="space-y-2 text-sm">
              <p>
                <span className="text-muted-foreground">Page:</span>{" "}
                <span className="font-mono">{getPathnameLabel(screenshot.url)}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Viewport/Browser:</span>{" "}
                <span className="font-mono">{formatViewportBrowser(screenshot)}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Status:</span>{" "}
                <span>
                  {screenshot.status === "ok"
                    ? "OK"
                    : screenshot.status === "recapturing"
                      ? "Recapturing…"
                      : "Failed"}
                </span>
              </p>
              <p>
                <span className="text-muted-foreground">Captured At:</span>{" "}
                <span>{formatDate(screenshot.created_at)}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Duration:</span>{" "}
                <span>{screenshot.duration_seconds.toFixed(2)}s</span>
              </p>
            </div>
          </MetadataCard>

          <MetadataCard label="Browser">
            <div className="flex items-center gap-2 text-sm">
              <BrowserIcon browser={screenshot.browser} size={14} />
              {browserLabel(screenshot.browser)}
            </div>
          </MetadataCard>

          <MetadataCard label="URL">
            <p className="break-words font-mono text-xs leading-5" title={screenshot.url}>
              {screenshot.url}
            </p>
          </MetadataCard>
        </aside>
      </div>
    </div>
  )
}
