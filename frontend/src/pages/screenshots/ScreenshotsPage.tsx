import { memo, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { useViewerState, type DisplayMode } from "@/hooks/useViewerState"
import { PageSidebar } from "@/components/screenshots/PageSidebar"
import { ViewportFilterBar } from "@/components/screenshots/ViewportFilterBar"
import { ScreenshotCard } from "@/components/screenshots/ScreenshotCard"
import { StateMessage } from "@/components/ui/StateMessage"
import type { Screenshot } from "@/types"

interface Props {
  taskId: string
  screenshots: Screenshot[]
}

interface ViewerRenderGroup {
  key: string
  url: string
  screenshots: Screenshot[]
}

function groupByUrl(screenshots: Screenshot[]): [string, Screenshot[]][] {
  const groups = new Map<string, Screenshot[]>()
  for (const s of screenshots) {
    try {
      const path = new URL(s.url).pathname
      if (!groups.has(path)) groups.set(path, [])
      groups.get(path)!.push(s)
    } catch {
      if (!groups.has(s.url)) groups.set(s.url, [])
      groups.get(s.url)!.push(s)
    }
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b))
}

function sortByViewport(shots: Screenshot[]): Screenshot[] {
  return [...shots].sort((a, b) => b.width - a.width || b.height - a.height)
}

function buildRenderGroups(screenshots: Screenshot[]): ViewerRenderGroup[] {
  return groupByUrl(screenshots).map(([path, shots]) => {
    const sorted = sortByViewport(shots)
    return {
      key: path,
      url: sorted[0]?.url ?? path,
      screenshots: sorted,
    }
  })
}

export default function ScreenshotsPage({ taskId, screenshots }: Props) {
  const navigate = useNavigate()
  const {
    viewerState,
    layoutMode,
    toggleLayoutMode,
    displayMode,
    setDisplayMode,
    activePage,
    selectedPages,
    selectedViewports,
    selectedDevices,
    selectedBrowsers,
    setPage,
    togglePage,
    clearPages,
    toggleViewport,
    clearViewports,
    toggleDevice,
    clearDevices,
    toggleBrowser,
    clearBrowsers,
    statusFilter,
    setStatusFilter,
    okCount,
    failedCount,
    uniquePages,
    uniqueViewports,
    availableDeviceTypes,
    availableBrowsers,
    filteredScreenshots,
    sidebarTotal,
  } = useViewerState(screenshots)

  const goToDetail = useCallback(
    (screenshotId: string) => navigate(`/tasks/${taskId}/screenshots/${screenshotId}`),
    [navigate, taskId],
  )

  const renderGroups = useMemo(
    () => buildRenderGroups(filteredScreenshots),
    [filteredScreenshots],
  )

  if (screenshots.length === 0) {
    return (
      <StateMessage
        compact
        title="No screenshots yet"
        description="They will appear here as the task runs."
      />
    )
  }

  const isPageMode = layoutMode === "sidebar" && viewerState.selectedPageGroup !== null

  const content = (
    <div className="flex-1 p-4 sm:p-5">
      {filteredScreenshots.length === 0 ? (
        <StateMessage
          compact
          title="No screenshots found"
          description="Try changing the current page, viewport, device, or browser filters."
        />
      ) : isPageMode ? (
        <SinglePageView
          screenshots={sortByViewport(filteredScreenshots)}
          onNavigate={goToDetail}
          displayMode={displayMode}
        />
      ) : (
        <GroupedView
          groups={renderGroups}
          onNavigate={goToDetail}
          displayMode={displayMode}
        />
      )}
    </div>
  )

  const filterBar = (
    <ViewportFilterBar
      layoutMode={layoutMode}
      onToggleLayout={toggleLayoutMode}
      displayMode={displayMode}
      onSetDisplayMode={setDisplayMode}
      pages={uniquePages}
      selectedPages={selectedPages}
      onTogglePage={togglePage}
      onClearPages={clearPages}
      viewports={uniqueViewports}
      selectedViewports={selectedViewports}
      onToggleViewport={toggleViewport}
      onClearViewports={clearViewports}
      availableDeviceTypes={availableDeviceTypes}
      selectedDevices={selectedDevices}
      onToggleDevice={toggleDevice}
      onClearDevices={clearDevices}
      availableBrowsers={availableBrowsers}
      selectedBrowsers={selectedBrowsers}
      onToggleBrowser={toggleBrowser}
      onClearBrowsers={clearBrowsers}
      statusFilter={statusFilter}
      onSetStatusFilter={setStatusFilter}
      okCount={okCount}
      failedCount={failedCount}
    />
  )

  if (layoutMode === "sidebar") {
    return (
      <div className="glass-card flex min-h-[520px] overflow-hidden">
        <PageSidebar
          pages={uniquePages}
          totalCount={sidebarTotal}
          activePage={activePage}
          onSelect={setPage}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          {filterBar}
          {content}
        </div>
      </div>
    )
  }

  return (
    <div className="glass-card flex min-h-[520px] flex-col overflow-hidden">
      {filterBar}
      {content}
    </div>
  )
}

const GroupedView = memo(function GroupedView({
  groups,
  onNavigate,
  displayMode,
}: {
  groups: ViewerRenderGroup[]
  onNavigate: (id: string) => void
  displayMode: DisplayMode
}) {
  return (
    <div className="space-y-5">
      {groups.map((group, i) => (
        <div key={group.key}>
          {i > 0 && <div className="mb-5 border-t surface-divider" />}
          <div className="flex flex-col gap-1 pb-3 sm:flex-row sm:items-center sm:justify-between">
            <a
              href={group.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="truncate font-mono text-sm font-medium text-foreground hover:underline underline-offset-2"
            >
              {group.url}
            </a>
            <span className="technical-caption">
              {group.screenshots.length} capture{group.screenshots.length === 1 ? "" : "s"}
            </span>
          </div>

          <ScreenshotCollection
            screenshots={group.screenshots}
            onNavigate={onNavigate}
            displayMode={displayMode}
            showViewportLabel={displayMode === "strip"}
          />
        </div>
      ))}
    </div>
  )
})

const SinglePageView = memo(function SinglePageView({
  screenshots,
  onNavigate,
  displayMode,
}: {
  screenshots: Screenshot[]
  onNavigate: (id: string) => void
  displayMode: DisplayMode
}) {
  return (
    <div className="space-y-4">
      <p className="technical-caption">
        {screenshots.length} viewport{screenshots.length !== 1 ? "s" : ""}
      </p>
      <ScreenshotCollection
        screenshots={screenshots}
        onNavigate={onNavigate}
        displayMode={displayMode}
        showViewportLabel
      />
    </div>
  )
})

const ScreenshotCollection = memo(function ScreenshotCollection({
  screenshots,
  onNavigate,
  displayMode,
  showViewportLabel,
}: {
  screenshots: Screenshot[]
  onNavigate: (id: string) => void
  displayMode: DisplayMode
  showViewportLabel: boolean
}) {
  if (displayMode === "strip") {
    return (
      <div className="strip-scroll-row">
        {screenshots.map((s) =>
          showViewportLabel ? (
            <div key={s.id} className="space-y-2">
              <p className="font-mono text-xs text-muted-foreground">
                {s.width}x{s.height}
              </p>
              <ScreenshotCard
                screenshot={s}
                onClick={() => onNavigate(s.id)}
                variant="strip"
              />
            </div>
          ) : (
            <ScreenshotCard
              key={s.id}
              screenshot={s}
              onClick={() => onNavigate(s.id)}
              variant="strip"
            />
          )
        )}
      </div>
    )
  }

  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
      {screenshots.map((s) => (
        <div key={s.id}>
          {showViewportLabel && (
            <p className="mb-2 font-mono text-xs text-muted-foreground">
              {s.width}x{s.height}
            </p>
          )}
          <ScreenshotCard
            screenshot={s}
            onClick={() => onNavigate(s.id)}
          />
        </div>
      ))}
    </div>
  )
})
