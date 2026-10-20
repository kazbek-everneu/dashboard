import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  AlertTriangle,
  Download,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
} from "lucide-react"
import { screenshotsApi } from "@/api/screenshots"
import { buildImgUrl } from "@/components/screenshots/screenshot-utils"
import { CaptureDetails } from "@/components/screenshots/CaptureDetails"
import { ScreenshotViewport } from "@/components/screenshots/ScreenshotViewport"
import { WarningsList } from "@/components/screenshots/WarningsList"
import PageHeader from "@/components/layout/PageHeader"
import { useBreadcrumbs } from "@/hooks/useBreadcrumbs"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { MetadataCard } from "@/components/ui/MetadataCard"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { StateMessage } from "@/components/ui/StateMessage"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { shortId, extractApiError } from "@/lib/helpers"
import { DateTime } from "@/components/ui/DateTime"
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
      // Leave keys to menus, fields and browser shortcuts that handle them.
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      if (target?.closest("input, textarea, select, [contenteditable='true'], [role='menu']")) return

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

  useBreadcrumbs([
    { label: "Tasks", to: "/tasks" },
    { label: `Task #${shortId(taskId ?? "")}`, to: `/tasks/${taskId}` },
    { label: "Screenshot" },
  ])

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-7 w-96 max-w-full" />
        <Skeleton className="h-4 w-32" />
        <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
          <Skeleton className="h-[480px]" />
          <Skeleton className="h-[320px]" />
        </div>
      </div>
    )
  }

  if (!screenshot) {
    return (
      <StateMessage
        compact
        tone="destructive"
        title="Screenshot not found"
        action={
          <Button asChild variant="outline">
            <Link to={`/tasks/${taskId}`}>Back to the task</Link>
          </Button>
        }
      />
    )
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
    <div className="flex flex-col gap-6">
      <PageHeader
        title={
          <h1 className="text-xl font-semibold tracking-tight break-all">{screenshot.url}</h1>
        }
        meta={`Capture ${currentIndex + 1} of ${screenshots.length}`}
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => recaptureMutation.mutate()}
              disabled={isRecapturing}
            >
              {isRecapturing ? <Spinner data-icon="inline-start" /> : <RotateCcw data-icon="inline-start" />}
              {isRecapturing ? "Recapturing…" : "Re-capture"}
            </Button>
            {screenshot.status === "ok" && screenshot.signed_url && (
              <Button asChild variant="outline">
                <a href={imgUrl} download={downloadFilename}>
                  <Download data-icon="inline-start" />
                  Download
                </a>
              </Button>
            )}
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
        <div className="flex flex-col gap-4">
          <ScreenshotViewport
            key={screenshot.id}
            screenshot={screenshot}
            imgUrl={imgUrl}
            isRefreshingSignedUrl={isRefreshingSignedUrl}
            onRequestSignedUrlRefresh={requestSignedUrlRefresh}
          />

          {screenshots.length > 1 && (
            <div className="flex items-center justify-between rounded-xl border bg-card px-3 py-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigateToIndex(prevIndex)}
                disabled={prevIndex === null}
              >
                <ChevronLeft data-icon="inline-start" />
                Previous
              </Button>

              <span className="text-sm tabular-nums text-muted-foreground">
                {currentIndex + 1} / {screenshots.length}
              </span>

              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigateToIndex(nextIndex)}
                disabled={nextIndex === null}
              >
                Next
                <ChevronRight data-icon="inline-end" />
              </Button>
            </div>
          )}
        </div>

        <aside className="flex flex-col gap-4">
          {screenshot.status === "failed" && (
            <Alert variant="destructive">
              <AlertTriangle />
              <AlertTitle>Capture failed</AlertTitle>
              <AlertDescription className="break-words">
                {screenshot.error_message ?? "Screenshot capture failed"}
              </AlertDescription>
            </Alert>
          )}

          <WarningsList screenshot={screenshot} />

          <MetadataCard label="Navigation">
            <div className="flex flex-col gap-2">
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-between"
                onClick={() => navigateToIndex(nextIndex)}
                disabled={nextIndex === null}
              >
                Next screenshot
                <Kbd>→</Kbd>
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-between"
                onClick={() => navigateToIndex(nextPageIndex)}
                disabled={nextPageIndex === null}
              >
                Next page
                <Kbd>]</Kbd>
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-between"
                onClick={() => navigateToIndex(nextViewportBrowserIndex)}
                disabled={nextViewportBrowserIndex === null}
              >
                Next viewport / browser
                <Kbd>[</Kbd>
              </Button>
              <p className="text-xs text-muted-foreground">
                Previous screenshot: <Kbd>←</Kbd>
              </p>
            </div>
          </MetadataCard>

          <MetadataCard label="Capture">
            <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">Page</dt>
              <dd className="truncate font-mono text-xs" title={getPathnameLabel(screenshot.url)}>
                {getPathnameLabel(screenshot.url)}
              </dd>
              <dt className="text-muted-foreground">Viewport</dt>
              <dd className="font-mono text-xs">
                {screenshot.width}×{screenshot.height}
              </dd>
              <dt className="text-muted-foreground">Browser</dt>
              <dd className="flex min-w-0 items-center gap-2">
                <BrowserIcon browser={screenshot.browser} size={14} />
                {browserLabel(screenshot.browser)}
                {screenshot.capture_meta?.browser_version && (
                  <span className="truncate font-mono text-xs text-muted-foreground">
                    {screenshot.capture_meta.browser_version}
                  </span>
                )}
              </dd>
              <dt className="text-muted-foreground">Status</dt>
              <dd>
                <StatusBadge status={screenshot.status} />
              </dd>
              <dt className="text-muted-foreground">Captured</dt>
              <dd><DateTime value={screenshot.created_at} /></dd>
              <dt className="text-muted-foreground">Duration</dt>
              <dd className="tabular-nums">{screenshot.duration_seconds.toFixed(2)}s</dd>
              <CaptureDetails screenshot={screenshot} />
            </dl>
          </MetadataCard>

          <MetadataCard label="URL">
            <a
              href={screenshot.url}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all font-mono text-xs leading-5 underline-offset-2 hover:underline"
            >
              {screenshot.url}
            </a>
          </MetadataCard>
        </aside>
      </div>
    </div>
  )
}
