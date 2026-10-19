import { useState } from "react"
import { Monitor, Tablet, Smartphone, PanelLeft, AlertTriangle, CheckCircle2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { Checkbox } from "@/components/ui/checkbox"
import { FilterToolbar } from "@/components/ui/FilterToolbar"
import { MenuSurface } from "@/components/ui/MenuSurface"
import type {
  ViewportItem,
  BrowserItem,
  DeviceItem,
  PageItem,
  StatusFilter,
} from "@/hooks/useScreenshotFilters"

interface Props {
  // Layout toggle
  layoutMode: "sidebar" | "dropdown"
  onToggleLayout: () => void
  // Screenshot view toggle
  displayMode: "grid" | "strip"
  onSetDisplayMode: (mode: "grid" | "strip") => void
  // Pages (only used in dropdown mode)
  pages: PageItem[]
  selectedPages: string[]
  onTogglePage: (url: string) => void
  onClearPages: () => void
  // Viewports
  viewports: ViewportItem[]
  selectedViewports: string[]
  onToggleViewport: (key: string) => void
  onClearViewports: () => void
  // Devices
  availableDeviceTypes: DeviceItem[]
  selectedDevices: string[]
  onToggleDevice: (device: string) => void
  onClearDevices: () => void
  // Browsers
  availableBrowsers: BrowserItem[]
  selectedBrowsers: string[]
  onToggleBrowser: (browser: string) => void
  onClearBrowsers: () => void
  // Status (ok / failed)
  statusFilter: StatusFilter
  onSetStatusFilter: (status: StatusFilter) => void
  okCount: number
  failedCount: number
}

const deviceIcons = {
  desktop: Monitor,
  tablet: Tablet,
  mobile: Smartphone,
}

function browserLabel(browser: string): string {
  if (browser === "msedge") return "Edge"
  if (browser === "webkit") return "Safari"
  return browser.charAt(0).toUpperCase() + browser.slice(1)
}

export function ViewportFilterBar({
  layoutMode,
  onToggleLayout,
  displayMode,
  onSetDisplayMode,
  pages,
  selectedPages,
  onTogglePage,
  onClearPages,
  viewports,
  selectedViewports,
  onToggleViewport,
  onClearViewports,
  availableDeviceTypes,
  selectedDevices,
  onToggleDevice,
  onClearDevices,
  availableBrowsers,
  selectedBrowsers,
  onToggleBrowser,
  onClearBrowsers,
  statusFilter,
  onSetStatusFilter,
  okCount,
  failedCount,
}: Props) {
  return (
    <FilterToolbar
      className="border-0 border-b surface-divider bg-[var(--surface-1)] shadow-none"
      filters={
        <>
          <StatusFilterControl
            statusFilter={statusFilter}
            onSetStatusFilter={onSetStatusFilter}
            okCount={okCount}
            failedCount={failedCount}
          />
          {layoutMode === "dropdown" && (
            <PagesDropdown
              pages={pages}
              selectedPages={selectedPages}
              onToggle={onTogglePage}
              onClear={onClearPages}
            />
          )}
          <ViewportDropdown
            viewports={viewports}
            selectedViewports={selectedViewports}
            onToggle={onToggleViewport}
            onClear={onClearViewports}
          />
          {availableDeviceTypes.length > 1 && (
            <DeviceDropdown
              devices={availableDeviceTypes}
              selectedDevices={selectedDevices}
              onToggle={onToggleDevice}
              onClear={onClearDevices}
            />
          )}
          {availableBrowsers.length > 1 && (
            <BrowserDropdown
              browsers={availableBrowsers}
              selectedBrowsers={selectedBrowsers}
              onToggle={onToggleBrowser}
              onClear={onClearBrowsers}
            />
          )}
        </>
      }
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden border">
            <button
              type="button"
              onClick={() => onSetDisplayMode("grid")}
              title="Show screenshots in grid view"
              className={cn(
                "px-3 py-2 text-xs font-medium transition-colors",
                displayMode === "grid"
                  ? "bg-background text-foreground"
                  : "bg-background text-[color:var(--text-secondary)] hover:bg-muted hover:text-foreground",
              )}
            >
              Grid
            </button>
            <button
              type="button"
              onClick={() => onSetDisplayMode("strip")}
              title="Show screenshots in horizontal strip view"
              className={cn(
                "border-l px-3 py-2 text-xs font-medium transition-colors",
                displayMode === "strip"
                  ? "bg-background text-foreground"
                  : "bg-background text-[color:var(--text-secondary)] hover:bg-muted hover:text-foreground",
              )}
            >
              Horizontal strip
            </button>
          </div>

          <button
            type="button"
            onClick={onToggleLayout}
            title={layoutMode === "sidebar" ? "Switch to dropdown mode" : "Switch to sidebar mode"}
            className={cn(
              "border px-3 py-2 text-xs font-medium transition-colors",
              layoutMode === "sidebar"
                ? "bg-background text-foreground"
                : "bg-background text-[color:var(--text-secondary)] hover:bg-muted hover:text-foreground",
            )}
          >
            <span className="flex items-center gap-2">
              <PanelLeft size={14} />
              {layoutMode === "sidebar" ? "Sidebar" : "Dropdown"}
            </span>
          </button>
        </div>
      }
    />
  )
}

// ── Shared dropdown shell ────────────────────────────────────────────────────

// ── Status segmented control (All / OK / Failed) ─────────────────────────────

function StatusFilterControl({
  statusFilter,
  onSetStatusFilter,
  okCount,
  failedCount,
}: {
  statusFilter: StatusFilter
  onSetStatusFilter: (status: StatusFilter) => void
  okCount: number
  failedCount: number
}) {
  const base = "flex items-center gap-1.5 px-3 py-2 text-xs font-medium transition-colors"
  const active = "bg-foreground text-background"
  const inactive =
    "bg-background text-[color:var(--text-secondary)] hover:bg-muted hover:text-foreground"

  return (
    <div className="flex overflow-hidden border">
      <button
        type="button"
        onClick={() => onSetStatusFilter("all")}
        title="Show all screenshots"
        className={cn(base, statusFilter === "all" ? active : inactive)}
      >
        All
      </button>
      <button
        type="button"
        onClick={() => onSetStatusFilter("ok")}
        title="Show successful captures only"
        className={cn(base, "border-l", statusFilter === "ok" ? active : inactive)}
      >
        <CheckCircle2 size={12} />
        OK
        <span className="tabular-nums opacity-70">{okCount}</span>
      </button>
      {failedCount > 0 && (
        <button
          type="button"
          onClick={() => onSetStatusFilter("failed")}
          title="Show failed captures only"
          className={cn(
            base,
            "border-l",
            statusFilter === "failed"
              ? "bg-destructive text-destructive-foreground"
              : "bg-background text-destructive hover:bg-destructive/10",
          )}
        >
          <AlertTriangle size={12} />
          Failed
          <span className="tabular-nums opacity-80">{failedCount}</span>
        </button>
      )}
    </div>
  )
}

// ── Pages dropdown (dropdown mode only) ─────────────────────────────────────

function PagesDropdown({
  pages,
  selectedPages,
  onToggle,
  onClear,
}: {
  pages: PageItem[]
  selectedPages: string[]
  onToggle: (url: string) => void
  onClear: () => void
}) {
  const [search, setSearch] = useState("")

  const allPagesSelected =
    selectedPages.length === 0 || selectedPages.length === pages.length
  const somePagesSelected =
    selectedPages.length > 0 && selectedPages.length < pages.length

  let triggerLabel: React.ReactNode
  if (allPagesSelected) {
    triggerLabel = <span>All Pages</span>
  } else if (selectedPages.length === 1) {
    const page = pages.find((p) => p.url === selectedPages[0])
    triggerLabel = (
      <span className="font-mono max-w-[140px] truncate">
        {page?.label ?? selectedPages[0]}
      </span>
    )
  } else {
    triggerLabel = <span>{selectedPages.length} Pages</span>
  }

  const allCheckedState: boolean | "indeterminate" = somePagesSelected
    ? "indeterminate"
    : true

  const filtered = search
    ? pages.filter((p) => p.url.toLowerCase().includes(search.toLowerCase()))
    : pages

  return (
    <MenuSurface trigger={triggerLabel}>
      {/* Search input */}
      <div className="px-2 pt-1 pb-1">
        <input
          type="text"
          placeholder="Search pages…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full border border-border bg-background px-2 py-1 text-xs outline-none focus:border-foreground/40"
          onClick={(e) => e.stopPropagation()}
        />
      </div>

      {/* "All" row */}
      <label className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs">
        <Checkbox
          checked={allCheckedState}
          onCheckedChange={() => onClear()}
        />
        <span>All</span>
      </label>

      <div className="max-h-[260px] overflow-y-auto">
        {filtered.map((page) => {
          const checked = selectedPages.includes(page.url)
          return (
            <label
              key={page.url}
              className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs"
              title={page.url}
            >
              <Checkbox
                checked={checked}
                onCheckedChange={() => onToggle(page.url)}
              />
              {page.hasFailed && (
                <span className="h-1.5 w-1.5 shrink-0 bg-destructive" />
              )}
              <span className="font-mono flex-1 truncate">{page.label}</span>
              <span className="text-muted-foreground shrink-0">({page.count})</span>
            </label>
          )
        })}
        {filtered.length === 0 && (
          <p className="px-3 py-2 text-xs text-muted-foreground">No pages match.</p>
        )}
      </div>
    </MenuSurface>
  )
}

// ── Viewport dropdown ────────────────────────────────────────────────────────

function ViewportDropdown({
  viewports,
  selectedViewports,
  onToggle,
  onClear,
}: {
  viewports: ViewportItem[]
  selectedViewports: string[]
  onToggle: (key: string) => void
  onClear: () => void
}) {
  const allViewportsSelected =
    selectedViewports.length === 0 || selectedViewports.length === viewports.length

  let triggerLabel: React.ReactNode
  if (allViewportsSelected) {
    triggerLabel = <span>All Viewports</span>
  } else if (selectedViewports.length === 1) {
    const vp = viewports.find((v) => v.key === selectedViewports[0])
    triggerLabel = <span className="font-mono">{vp?.label ?? selectedViewports[0]}</span>
  } else {
    triggerLabel = <span>{selectedViewports.length} Viewports</span>
  }

  const allChecked = selectedViewports.length === 0

  return (
    <MenuSurface trigger={triggerLabel}>
      {/* "All" row */}
      <label className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs">
        <Checkbox
          checked={allChecked}
          onCheckedChange={() => onClear()}
        />
        <span>All</span>
      </label>

      {viewports.map((vp) => {
        const checked = selectedViewports.includes(vp.key)
        return (
          <label
            key={vp.key}
            className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs"
          >
            <Checkbox
              checked={checked}
              onCheckedChange={() => onToggle(vp.key)}
            />
            <span className="font-mono flex-1">{vp.label}</span>
            <span className="text-muted-foreground">({vp.count})</span>
          </label>
        )
      })}
    </MenuSurface>
  )
}

// ── Device dropdown ──────────────────────────────────────────────────────────

function DeviceDropdown({
  devices,
  selectedDevices,
  onToggle,
  onClear,
}: {
  devices: DeviceItem[]
  selectedDevices: string[]
  onToggle: (device: string) => void
  onClear: () => void
}) {
  const allDevicesSelected =
    selectedDevices.length === 0 || selectedDevices.length === devices.length
  const someDevicesSelected =
    selectedDevices.length > 0 && selectedDevices.length < devices.length

  let triggerLabel: React.ReactNode
  if (allDevicesSelected) {
    triggerLabel = <span>All Devices</span>
  } else if (selectedDevices.length === 1) {
    const d = selectedDevices[0] as keyof typeof deviceIcons
    const Icon = deviceIcons[d] ?? Monitor
    triggerLabel = (
      <span className="flex items-center gap-1.5">
        <Icon size={12} />
        {d.charAt(0).toUpperCase() + d.slice(1)}
      </span>
    )
  } else {
    triggerLabel = <span>{selectedDevices.length} Devices</span>
  }

  const allCheckedState: boolean | "indeterminate" = someDevicesSelected
    ? "indeterminate"
    : true

  return (
    <MenuSurface trigger={triggerLabel}>
      {/* "All" row */}
      <label className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs">
        <Checkbox
          checked={allCheckedState}
          onCheckedChange={() => onClear()}
        />
        <span>All</span>
      </label>

      {devices.map(({ device, count }) => {
        const checked = selectedDevices.includes(device)
        const Icon = deviceIcons[device]
        return (
          <label
            key={device}
            className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs"
          >
            <Checkbox
              checked={checked}
              onCheckedChange={() => onToggle(device)}
            />
            <Icon size={12} className="text-muted-foreground" />
            <span className="flex-1">{device.charAt(0).toUpperCase() + device.slice(1)}</span>
            <span className="text-muted-foreground">({count})</span>
          </label>
        )
      })}
    </MenuSurface>
  )
}

// ── Browser dropdown ─────────────────────────────────────────────────────────

function BrowserDropdown({
  browsers,
  selectedBrowsers,
  onToggle,
  onClear,
}: {
  browsers: BrowserItem[]
  selectedBrowsers: string[]
  onToggle: (browser: string) => void
  onClear: () => void
}) {
  const allBrowsersSelected =
    selectedBrowsers.length === 0 || selectedBrowsers.length === browsers.length
  const someBrowsersSelected =
    selectedBrowsers.length > 0 && selectedBrowsers.length < browsers.length

  let triggerLabel: React.ReactNode
  if (allBrowsersSelected) {
    triggerLabel = <span>All Browsers</span>
  } else if (selectedBrowsers.length === 1) {
    triggerLabel = (
      <span className="flex items-center gap-1.5">
        <BrowserIcon browser={selectedBrowsers[0]} size={14} />
        {browserLabel(selectedBrowsers[0])}
      </span>
    )
  } else {
    triggerLabel = <span>{selectedBrowsers.length} Browsers</span>
  }

  const allCheckedState: boolean | "indeterminate" = someBrowsersSelected
    ? "indeterminate"
    : true

  return (
    <MenuSurface trigger={triggerLabel}>
      {/* "All" row */}
      <label className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs">
        <Checkbox
          checked={allCheckedState}
          onCheckedChange={() => onClear()}
        />
        <span>All</span>
      </label>

      {browsers.map(({ browser, count }) => {
        const checked = selectedBrowsers.includes(browser)
        return (
          <label
            key={browser}
            className="flex items-center gap-2.5 px-3 py-1.5 cursor-pointer hover:bg-accent text-xs"
          >
            <Checkbox
              checked={checked}
              onCheckedChange={() => onToggle(browser)}
            />
            <BrowserIcon browser={browser} size={14} />
            <span className="flex-1">{browserLabel(browser)}</span>
            <span className="text-muted-foreground">({count})</span>
          </label>
        )
      })}
    </MenuSurface>
  )
}
