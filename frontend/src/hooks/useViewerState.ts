import { useCallback, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useScreenshotFilters } from "@/hooks/useScreenshotFilters"
import type { Screenshot } from "@/types"

type LayoutMode = "sidebar" | "dropdown"
export type DisplayMode = "grid" | "strip"

const LAYOUT_STORAGE_KEY = "qa-screenshot-layout"
const DISPLAY_STORAGE_KEY = "qa-screenshot-display-mode"
const LEGACY_VIEW_STORAGE_KEY = "qa-screenshot-view"

function readLayoutMode(searchParams: URLSearchParams): LayoutMode {
  const fromUrl = searchParams.get("layout")
  if (fromUrl === "dropdown" || fromUrl === "sidebar") return fromUrl

  const fromStorage = localStorage.getItem(LAYOUT_STORAGE_KEY)
  if (fromStorage === "dropdown" || fromStorage === "sidebar") return fromStorage

  return "sidebar"
}

function readDisplayMode(searchParams: URLSearchParams): DisplayMode {
  const fromUrl = searchParams.get("display")
  if (fromUrl === "grid" || fromUrl === "strip") return fromUrl

  // Backward-compatible query support during migration from "view" to "display".
  const fromLegacyUrl = searchParams.get("view")
  if (fromLegacyUrl === "grid" || fromLegacyUrl === "strip") return fromLegacyUrl

  const fromStorage = localStorage.getItem(DISPLAY_STORAGE_KEY)
  if (fromStorage === "grid" || fromStorage === "strip") return fromStorage

  const fromLegacyStorage = localStorage.getItem(LEGACY_VIEW_STORAGE_KEY)
  if (fromLegacyStorage === "grid" || fromLegacyStorage === "strip") return fromLegacyStorage

  return "grid"
}

export interface ViewerState {
  selectedPageGroup: string | null
  viewportFilters: string[]
  browserFilters: string[]
}

export function useViewerState(screenshots: Screenshot[]) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [layoutMode, setLayoutMode] = useState<LayoutMode>(() => readLayoutMode(searchParams))
  const [displayMode, setDisplayModeValue] = useState<DisplayMode>(() =>
    readDisplayMode(searchParams),
  )

  const filters = useScreenshotFilters(screenshots)

  const selectedPageGroup = useMemo(
    () => filters.activePage ?? filters.selectedPages[0] ?? null,
    [filters.activePage, filters.selectedPages],
  )

  const toggleLayoutMode = useCallback(() => {
    const next = layoutMode === "sidebar" ? "dropdown" : "sidebar"
    setLayoutMode(next)
    localStorage.setItem(LAYOUT_STORAGE_KEY, next)
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.set("layout", next)
        p.delete("page")
        p.delete("pages")
        return p
      },
      { replace: true },
    )
  }, [layoutMode, setSearchParams])

  const setDisplayMode = useCallback(
    (next: DisplayMode) => {
      if (next === displayMode) return

      setDisplayModeValue(next)
      localStorage.setItem(DISPLAY_STORAGE_KEY, next)

      setSearchParams(
        (prev) => {
          const p = new URLSearchParams(prev)
          p.set("display", next)
          p.delete("view")
          return p
        },
        { replace: true },
      )
    },
    [displayMode, setSearchParams],
  )

  const viewerState = useMemo<ViewerState>(
    () => ({
      selectedPageGroup,
      viewportFilters: filters.selectedViewports,
      browserFilters: filters.selectedBrowsers,
    }),
    [
      selectedPageGroup,
      filters.selectedViewports,
      filters.selectedBrowsers,
    ],
  )

  return {
    viewerState,
    layoutMode,
    toggleLayoutMode,
    displayMode,
    setDisplayMode,
    ...filters,
  }
}
