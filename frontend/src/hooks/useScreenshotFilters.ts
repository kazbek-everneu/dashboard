import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import { getDeviceType } from "@/lib/helpers"
import type { Screenshot } from "@/types"

export interface PageItem {
  url: string
  label: string
  count: number
  hasFailed: boolean
}

export interface ViewportItem {
  key: string   // "1920x1080" — used in URL params
  label: string // "1920×1080" — displayed
  width: number
  height: number
  count: number
}

export interface BrowserItem {
  browser: string
  count: number
}

export interface DeviceItem {
  device: "desktop" | "tablet" | "mobile"
  count: number
}

export type StatusFilter = "all" | "ok" | "failed"

const DEVICE_ORDER = ["desktop", "tablet", "mobile"] as const

function getPathLabel(url: string): string {
  try {
    const path = new URL(url).pathname
    const segments = path.split("/").filter(Boolean)
    if (segments.length === 0) return "home"
    return segments[segments.length - 1]
  } catch {
    return url
  }
}

export function useScreenshotFilters(screenshots: Screenshot[]) {
  const [searchParams, setSearchParams] = useSearchParams()

  const activePage = searchParams.get("page")         // full URL string or null (sidebar single-select)
  const pagesParam = searchParams.get("pages")         // "url1,url2" or null (dropdown multi-select)
  const viewportsParam = searchParams.get("viewports") // "1920x1080,1280x800" or null
  const devicesParam = searchParams.get("devices")     // "desktop,mobile" or null
  const browsersParam = searchParams.get("browsers")   // "chrome,firefox" or null
  const statusParam = searchParams.get("status")        // "ok" | "failed" or null

  const statusFilter: StatusFilter =
    statusParam === "ok" || statusParam === "failed" ? statusParam : "all"

  const selectedPages: string[] = useMemo(
    () => (pagesParam ? pagesParam.split(",").filter(Boolean) : []),
    [pagesParam],
  )

  const selectedViewports: string[] = useMemo(
    () => (viewportsParam ? viewportsParam.split(",").filter(Boolean) : []),
    [viewportsParam],
  )

  const selectedDevices: string[] = useMemo(
    () => (devicesParam ? devicesParam.split(",").filter(Boolean) : []),
    [devicesParam],
  )

  const selectedBrowsers: string[] = useMemo(
    () => (browsersParam ? browsersParam.split(",").filter(Boolean) : []),
    [browsersParam],
  )

  function setPage(url: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (url !== null) next.set("page", url)
        else next.delete("page")
        return next
      },
      { replace: true },
    )
  }

  function togglePage(url: string) {
    const next = selectedPages.includes(url)
      ? selectedPages.filter((u) => u !== url)
      : [...selectedPages, url]
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next.length > 0) p.set("pages", next.join(","))
        else p.delete("pages")
        return p
      },
      { replace: true },
    )
  }

  function clearPages() {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.delete("pages")
        return p
      },
      { replace: true },
    )
  }

  function toggleViewport(key: string) {
    const next = selectedViewports.includes(key)
      ? selectedViewports.filter((k) => k !== key)
      : [...selectedViewports, key]
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next.length > 0) p.set("viewports", next.join(","))
        else p.delete("viewports")
        return p
      },
      { replace: true },
    )
  }

  function clearViewports() {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.delete("viewports")
        return p
      },
      { replace: true },
    )
  }

  function toggleDevice(device: string) {
    const next = selectedDevices.includes(device)
      ? selectedDevices.filter((d) => d !== device)
      : [...selectedDevices, device]
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next.length > 0) p.set("devices", next.join(","))
        else p.delete("devices")
        return p
      },
      { replace: true },
    )
  }

  function clearDevices() {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.delete("devices")
        return p
      },
      { replace: true },
    )
  }

  function clearBrowsers() {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        p.delete("browsers")
        return p
      },
      { replace: true },
    )
  }

  function toggleBrowser(browser: string) {
    const next = selectedBrowsers.includes(browser)
      ? selectedBrowsers.filter((b) => b !== browser)
      : [...selectedBrowsers, browser]
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next.length > 0) p.set("browsers", next.join(","))
        else p.delete("browsers")
        return p
      },
      { replace: true },
    )
  }

  function setStatusFilter(next: StatusFilter) {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next === "all") p.delete("status")
        else p.set("status", next)
        return p
      },
      { replace: true },
    )
  }

  // ── OK / failed totals — computed across every screenshot (ignores filters) ──
  const { okCount, failedCount } = useMemo(() => {
    let ok = 0
    let failed = 0
    for (const s of screenshots) {
      if (s.status === "failed") failed += 1
      else if (s.status === "ok") ok += 1
      // "recapturing" is transient — counted in neither bucket.
    }
    return { okCount: ok, failedCount: failed }
  }, [screenshots])

  // ── Viewport items — counts filtered by devices + browsers (not viewport or page) ──
  const uniqueViewports = useMemo<ViewportItem[]>(() => {
    const map = new Map<string, { width: number; height: number; count: number }>()
    for (const s of screenshots) {
      if (selectedDevices.length > 0 && !selectedDevices.includes(getDeviceType(s.width))) continue
      if (selectedBrowsers.length > 0 && !selectedBrowsers.includes(s.browser)) continue
      const key = `${s.width}x${s.height}`
      const entry = map.get(key)
      if (entry) entry.count++
      else map.set(key, { width: s.width, height: s.height, count: 1 })
    }
    return Array.from(map.entries())
      .map(([key, { width, height, count }]) => ({
        key,
        label: `${width}×${height}`,
        width,
        height,
        count,
      }))
      .sort((a, b) => b.width - a.width || b.height - a.height)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots, devicesParam, browsersParam])

  // ── Device items — counts filtered by viewports + browsers (not device or page) ──
  const availableDeviceTypes = useMemo<DeviceItem[]>(() => {
    const map = new Map<string, number>()
    for (const s of screenshots) {
      if (selectedViewports.length > 0 && !selectedViewports.includes(`${s.width}x${s.height}`)) continue
      if (selectedBrowsers.length > 0 && !selectedBrowsers.includes(s.browser)) continue
      const dt = getDeviceType(s.width)
      map.set(dt, (map.get(dt) ?? 0) + 1)
    }
    return DEVICE_ORDER
      .filter((d) => map.has(d))
      .map((d) => ({ device: d, count: map.get(d)! }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots, viewportsParam, browsersParam])

  // ── Browser items — counts filtered by viewports + devices (not browser or page) ──
  const availableBrowsers = useMemo<BrowserItem[]>(() => {
    const map = new Map<string, number>()
    for (const s of screenshots) {
      if (!s.browser) continue
      if (selectedViewports.length > 0 && !selectedViewports.includes(`${s.width}x${s.height}`)) continue
      if (selectedDevices.length > 0 && !selectedDevices.includes(getDeviceType(s.width))) continue
      map.set(s.browser, (map.get(s.browser) ?? 0) + 1)
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([browser, count]) => ({ browser, count }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots, viewportsParam, devicesParam])

  // ── Sidebar pages — counts filtered by viewport + device + browser (not page) ──
  const uniquePages = useMemo<PageItem[]>(() => {
    const map = new Map<string, { count: number; hasFailed: boolean }>()
    for (const s of screenshots) {
      if (selectedViewports.length > 0 && !selectedViewports.includes(`${s.width}x${s.height}`)) continue
      if (selectedDevices.length > 0 && !selectedDevices.includes(getDeviceType(s.width))) continue
      if (selectedBrowsers.length > 0 && !selectedBrowsers.includes(s.browser)) continue
      const entry = map.get(s.url)
      if (entry) {
        entry.count++
        if (s.status === "failed") entry.hasFailed = true
      } else {
        map.set(s.url, { count: 1, hasFailed: s.status === "failed" })
      }
    }
    return Array.from(map.entries()).map(([url, data]) => ({
      url,
      label: getPathLabel(url),
      count: data.count,
      hasFailed: data.hasFailed,
    }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots, viewportsParam, devicesParam, browsersParam])

  // ── Filtered screenshots — all active filters applied ─────────────────────────
  const filteredScreenshots = useMemo(() => {
    return screenshots.filter((s) => {
      if (activePage !== null && s.url !== activePage) return false
      if (selectedPages.length > 0 && !selectedPages.includes(s.url)) return false
      if (selectedViewports.length > 0 && !selectedViewports.includes(`${s.width}x${s.height}`)) return false
      if (selectedDevices.length > 0 && !selectedDevices.includes(getDeviceType(s.width))) return false
      if (selectedBrowsers.length > 0 && !selectedBrowsers.includes(s.browser)) return false
      // Keep in-flight recaptures visible under any status filter so the
      // spinner (and its eventual result) never disappears mid-recapture.
      if (statusFilter !== "all" && s.status !== statusFilter && s.status !== "recapturing")
        return false
      return true
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screenshots, activePage, pagesParam, viewportsParam, devicesParam, browsersParam, statusParam])

  // ── Sidebar "All" count — total matching all filters except page ───────────────
  const sidebarTotal = useMemo(
    () => uniquePages.reduce((sum, p) => sum + p.count, 0),
    [uniquePages],
  )

  return {
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
    totalCount: screenshots.length,
    sidebarTotal,
  }
}
