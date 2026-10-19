import { useState, useRef } from "react"
import { Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { DeviceTypeToggle } from "./DeviceTypeToggle"
import { BrowserMultiSelect } from "./BrowserMultiSelect"
import type { ViewportConfig } from "@/types"
import type { DeviceType } from "./DeviceTypeToggle"

export interface DraftViewportRowProps {
  defaultBrowsers: string[]
  onCommit: (viewport: ViewportConfig) => void
  onDiscard: () => void
}

const NO_SPINNER =
  "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"

const INPUT_BASE =
  "h-8 w-[5ch] border bg-background px-1 text-center text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-ring"

function detectDeviceType(width: number): DeviceType {
  if (width >= 1024) return "desktop"
  if (width >= 768) return "tablet"
  return "mobile"
}

export function DraftViewportRow({ defaultBrowsers, onCommit, onDiscard }: DraftViewportRowProps) {
  const [widthStr, setWidthStr] = useState("")
  const [heightStr, setHeightStr] = useState("")
  const [deviceType, setDeviceType] = useState<DeviceType | null>(null)
  const [browsers, setBrowsers] = useState<string[] | null>(null)
  const [widthError, setWidthError] = useState(false)
  const [heightError, setHeightError] = useState(false)

  // Track whether the user has manually chosen a device type
  const manualDeviceType = useRef(false)

  function handleWidthChange(raw: string) {
    setWidthStr(raw)
    setWidthError(false)
    if (!manualDeviceType.current) {
      const w = parseInt(raw, 10)
      if (!isNaN(w)) setDeviceType(detectDeviceType(w))
    }
  }

  function handleDeviceTypeChange(dt: DeviceType) {
    manualDeviceType.current = true
    setDeviceType(dt)
  }

  function tryCommit() {
    const wEmpty = widthStr === ""
    const hEmpty = heightStr === ""

    // Both empty → silently discard
    if (wEmpty && hEmpty) {
      onDiscard()
      return
    }

    const w = parseInt(widthStr, 10)
    const h = parseInt(heightStr, 10)
    const wInvalid = wEmpty || isNaN(w) || w < 320 || w > 3840
    const hInvalid = hEmpty || isNaN(h) || h < 400 || h > 2160

    if (wInvalid || hInvalid) {
      setWidthError(wInvalid)
      setHeightError(hInvalid)
      return // keep row, show error rings
    }

    onCommit({
      width: w,
      height: h,
      device_type: deviceType ?? detectDeviceType(w),
      browsers,
    })
  }

  // Container-level blur: fires when focus leaves the entire row div.
  // relatedTarget is the element receiving focus next.
  function handleContainerBlur(e: React.FocusEvent<HTMLDivElement>) {
    if (e.currentTarget.contains(e.relatedTarget)) return
    tryCommit()
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault()
      tryCommit()
    }
    if (e.key === "Escape") {
      onDiscard()
    }
  }

  return (
    <div
      onBlur={handleContainerBlur}
      onKeyDown={handleKeyDown}
      className="flex items-center gap-3 py-2 px-3 hover:bg-muted/30 ring-1 ring-inset ring-foreground/20"
    >
      {/* Col 1 — device type (none selected) */}
      <DeviceTypeToggle value={deviceType} onChange={handleDeviceTypeChange} />

      {/* Col 2 — resolution (empty inputs) */}
      <div className="flex items-center shrink-0 gap-1">
        <input
          type="number"
          value={widthStr}
          placeholder="W"
          min={320}
          max={3840}
          onChange={(e) => handleWidthChange(e.target.value)}
          title={widthError ? "Width must be 320–3840" : "Width (px)"}
          autoFocus
          className={cn(
            INPUT_BASE,
            NO_SPINNER,
            widthError ? "ring-1 ring-destructive border-destructive" : "border-border",
          )}
        />
        <span className="text-xs text-muted-foreground select-none">×</span>
        <input
          type="number"
          value={heightStr}
          placeholder="H"
          min={400}
          max={2160}
          onChange={(e) => {
            setHeightStr(e.target.value)
            setHeightError(false)
          }}
          title={heightError ? "Height must be 400–2160" : "Height (px)"}
          className={cn(
            INPUT_BASE,
            NO_SPINNER,
            heightError ? "ring-1 ring-destructive border-destructive" : "border-border",
          )}
        />
      </div>

      {/* Col 3 — browser (pre-set to default) */}
      <BrowserMultiSelect
        selected={browsers}
        defaultBrowsers={defaultBrowsers}
        onChange={setBrowsers}
        className="flex-1"
      />

      {/* Col 4 — discard */}
      <button
        type="button"
        onClick={onDiscard}
        className="ml-auto shrink-0 p-1.5 text-muted-foreground transition-colors hover:text-destructive"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  )
}
