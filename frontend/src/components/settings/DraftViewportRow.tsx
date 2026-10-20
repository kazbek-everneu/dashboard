import { useState, useRef } from "react"
import { X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DeviceTypeToggle } from "./DeviceTypeToggle"
import { BrowserMultiSelect } from "./BrowserMultiSelect"
import { HEIGHT_RANGE_ERROR, RESOLUTION_INPUT_CLASS, WIDTH_RANGE_ERROR } from "./resolution"
import type { ViewportConfig } from "@/types"
import type { DeviceType } from "./DeviceTypeToggle"

export interface DraftViewportRowProps {
  defaultBrowsers: string[]
  onCommit: (viewport: ViewportConfig) => void
  onDiscard: () => void
}

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
      return // keep row, show errors
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

  const errorText = [widthError && WIDTH_RANGE_ERROR, heightError && HEIGHT_RANGE_ERROR]
    .filter(Boolean)
    .join(" · ")

  return (
    <div
      onBlur={handleContainerBlur}
      onKeyDown={handleKeyDown}
      className="flex flex-col gap-1 px-3 py-2 ring-1 ring-inset ring-foreground/20 hover:bg-muted/30"
    >
      <div className="flex flex-wrap items-center gap-2">
        {/* Lines the columns up with the drag handle of saved rows */}
        <span className="size-6 shrink-0" aria-hidden />

        {/* Device type (none selected) */}
        <DeviceTypeToggle value={deviceType} onChange={handleDeviceTypeChange} />

        {/* Resolution (empty inputs) */}
        <div className="flex shrink-0 items-center gap-1">
          <Input
            type="number"
            value={widthStr}
            placeholder="W"
            min={320}
            max={3840}
            onChange={(e) => handleWidthChange(e.target.value)}
            aria-label="Width (px)"
            aria-invalid={widthError || undefined}
            autoFocus
            className={RESOLUTION_INPUT_CLASS}
          />
          <span className="select-none text-xs text-muted-foreground">×</span>
          <Input
            type="number"
            value={heightStr}
            placeholder="H"
            min={400}
            max={2160}
            onChange={(e) => {
              setHeightStr(e.target.value)
              setHeightError(false)
            }}
            aria-label="Height (px)"
            aria-invalid={heightError || undefined}
            className={RESOLUTION_INPUT_CLASS}
          />
        </div>

        {/* Browsers (pre-set to default) + discard wrap together */}
        <div className="flex min-w-fit flex-1 items-center justify-between gap-2">
          <BrowserMultiSelect selected={browsers} defaultBrowsers={defaultBrowsers} onChange={setBrowsers} />

          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={onDiscard}
            aria-label="Discard viewport"
            className="text-muted-foreground hover:text-destructive"
          >
            <X />
          </Button>
        </div>
      </div>

      {errorText && (
        <p role="alert" className="pl-8 text-xs text-destructive">
          {errorText}
        </p>
      )}
    </div>
  )
}
