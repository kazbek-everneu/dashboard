import type { ReactNode } from "react"
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  GalleryHorizontal,
  LayoutGrid,
  Minus,
  Monitor,
  PanelLeft,
  Smartphone,
  Tablet,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { browserLabel } from "@/lib/task-formatters"
import { BrowserIcon } from "@/components/ui/BrowserIcon"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Toggle } from "@/components/ui/toggle"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
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

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/** "All" is checked when nothing (or everything) is selected, mixed otherwise. */
function allState(selected: number, total: number): boolean | "indeterminate" {
  return selected === 0 || selected === total ? true : "indeterminate"
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
    <div className="flex flex-col gap-2 border-b p-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          spacing={0}
          value={statusFilter}
          // Re-clicking the active item reports "" — keep the current filter.
          onValueChange={(value) => value && onSetStatusFilter(value as StatusFilter)}
          aria-label="Capture status"
        >
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          <ToggleGroupItem value="ok">
            <CheckCircle2 />
            OK
            <span className="tabular-nums text-muted-foreground">{okCount}</span>
          </ToggleGroupItem>
          {failedCount > 0 && (
            <ToggleGroupItem
              value="failed"
              className="text-destructive data-[state=on]:bg-destructive/10 data-[state=on]:text-destructive"
            >
              <AlertTriangle />
              Failed
              <span className="tabular-nums">{failedCount}</span>
            </ToggleGroupItem>
          )}
        </ToggleGroup>

        {layoutMode === "dropdown" && (
          <PagesFilter
            pages={pages}
            selectedPages={selectedPages}
            onToggle={onTogglePage}
            onClear={onClearPages}
          />
        )}

        <CheckboxMenu
          label={
            selectedViewports.length === 0 || selectedViewports.length === viewports.length
              ? "All viewports"
              : selectedViewports.length === 1
                ? (viewports.find((v) => v.key === selectedViewports[0])?.label ?? selectedViewports[0])
                : `${selectedViewports.length} viewports`
          }
          allChecked={allState(selectedViewports.length, viewports.length)}
          onClear={onClearViewports}
          items={viewports.map((vp) => ({
            key: vp.key,
            label: <span className="font-mono">{vp.label}</span>,
            count: vp.count,
            checked: selectedViewports.includes(vp.key),
            onToggle: () => onToggleViewport(vp.key),
          }))}
        />

        {availableDeviceTypes.length > 1 && (
          <CheckboxMenu
            label={
              selectedDevices.length === 0 || selectedDevices.length === availableDeviceTypes.length
                ? "All devices"
                : selectedDevices.length === 1
                  ? capitalize(selectedDevices[0])
                  : `${selectedDevices.length} devices`
            }
            allChecked={allState(selectedDevices.length, availableDeviceTypes.length)}
            onClear={onClearDevices}
            items={availableDeviceTypes.map(({ device, count }) => {
              const Icon = deviceIcons[device]
              return {
                key: device,
                label: (
                  <>
                    <Icon />
                    {capitalize(device)}
                  </>
                ),
                count,
                checked: selectedDevices.includes(device),
                onToggle: () => onToggleDevice(device),
              }
            })}
          />
        )}

        {availableBrowsers.length > 1 && (
          <CheckboxMenu
            label={
              selectedBrowsers.length === 0 || selectedBrowsers.length === availableBrowsers.length
                ? "All browsers"
                : selectedBrowsers.length === 1
                  ? browserLabel(selectedBrowsers[0])
                  : `${selectedBrowsers.length} browsers`
            }
            allChecked={allState(selectedBrowsers.length, availableBrowsers.length)}
            onClear={onClearBrowsers}
            items={availableBrowsers.map(({ browser, count }) => ({
              key: browser,
              label: (
                <>
                  <BrowserIcon browser={browser} size={14} />
                  {browserLabel(browser)}
                </>
              ),
              count,
              checked: selectedBrowsers.includes(browser),
              onToggle: () => onToggleBrowser(browser),
            }))}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          spacing={0}
          value={displayMode}
          onValueChange={(value) => value && onSetDisplayMode(value as "grid" | "strip")}
          aria-label="Screenshot layout"
        >
          <ToggleGroupItem value="grid" title="Show screenshots in a grid">
            <LayoutGrid />
            Grid
          </ToggleGroupItem>
          <ToggleGroupItem value="strip" title="Show screenshots in a horizontal strip">
            <GalleryHorizontal />
            Strip
          </ToggleGroupItem>
        </ToggleGroup>

        <Toggle
          variant="outline"
          size="sm"
          pressed={layoutMode === "sidebar"}
          onPressedChange={onToggleLayout}
          title={layoutMode === "sidebar" ? "Pick pages from a dropdown" : "Show pages in a sidebar"}
        >
          <PanelLeft />
          Pages sidebar
        </Toggle>
      </div>
    </div>
  )
}

// ── Checkbox dropdown (viewports / devices / browsers) ───────────────────────

interface CheckboxMenuItem {
  key: string
  label: ReactNode
  count: number
  checked: boolean
  onToggle: () => void
}

function CheckboxMenu({
  label,
  allChecked,
  onClear,
  items,
}: {
  label: ReactNode
  allChecked: boolean | "indeterminate"
  onClear: () => void
  items: CheckboxMenuItem[]
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          {label}
          <ChevronDown data-icon="inline-end" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-48">
        {/* preventDefault keeps the menu open while several boxes are ticked */}
        <DropdownMenuCheckboxItem
          checked={allChecked}
          onCheckedChange={onClear}
          onSelect={(event) => event.preventDefault()}
        >
          All
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        {items.map((item) => (
          <DropdownMenuCheckboxItem
            key={item.key}
            checked={item.checked}
            onCheckedChange={item.onToggle}
            onSelect={(event) => event.preventDefault()}
          >
            {item.label}
            <DropdownMenuShortcut className="tabular-nums">{item.count}</DropdownMenuShortcut>
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// ── Pages filter (dropdown mode only): searchable list ──────────────────────

function CheckIndicator({ state }: { state: boolean | "indeterminate" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-[4px] border",
        state ? "border-primary bg-primary text-primary-foreground" : "border-input",
      )}
    >
      {state === "indeterminate" ? <Minus className="size-3" /> : state ? <Check className="size-3" /> : null}
    </span>
  )
}

function PagesFilter({
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
  let triggerLabel: ReactNode
  if (selectedPages.length === 0 || selectedPages.length === pages.length) {
    triggerLabel = "All pages"
  } else if (selectedPages.length === 1) {
    const page = pages.find((p) => p.url === selectedPages[0])
    triggerLabel = <span className="max-w-[160px] truncate font-mono">{page?.label ?? selectedPages[0]}</span>
  } else {
    triggerLabel = `${selectedPages.length} pages`
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          {triggerLabel}
          <ChevronDown data-icon="inline-end" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <Command>
          <CommandInput placeholder="Search pages…" />
          <CommandList>
            <CommandEmpty>No pages match.</CommandEmpty>
            <CommandGroup>
              <CommandItem value="__all__" onSelect={onClear}>
                <CheckIndicator state={allState(selectedPages.length, pages.length)} />
                All pages
              </CommandItem>
            </CommandGroup>
            <CommandSeparator />
            <CommandGroup>
              {pages.map((page) => (
                <CommandItem
                  key={page.url}
                  value={page.url}
                  title={page.url}
                  onSelect={() => onToggle(page.url)}
                >
                  <CheckIndicator state={selectedPages.includes(page.url)} />
                  {page.hasFailed && (
                    <span className="size-1.5 shrink-0 rounded-full bg-destructive" aria-label="has failures" />
                  )}
                  <span className="flex-1 truncate font-mono text-xs">{page.label}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">{page.count}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
