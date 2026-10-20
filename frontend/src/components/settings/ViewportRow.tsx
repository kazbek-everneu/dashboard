import { useState } from "react"
import { GripVertical, Trash2 } from "lucide-react"
import { useSortable } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { DeviceTypeToggle } from "./DeviceTypeToggle"
import { ResolutionInputs } from "./ResolutionInputs"
import { BrowserMultiSelect } from "./BrowserMultiSelect"
import { validateResolution } from "./resolution"
import type { ViewportConfig } from "@/types"
import type { DeviceType } from "./DeviceTypeToggle"

export interface ViewportRowProps {
  id: string
  viewport: ViewportConfig
  index: number
  onChange: (index: number, updated: ViewportConfig) => void
  onRemove: (index: number) => void
  defaultBrowsers: string[]
  disableRemove: boolean
  isNew?: boolean
  isFlashing?: boolean
}

function detectDeviceType(width: number): DeviceType {
  if (width >= 1024) return "desktop"
  if (width >= 768) return "tablet"
  return "mobile"
}

export function ViewportRow({
  id,
  viewport,
  index,
  onChange,
  onRemove,
  defaultBrowsers,
  disableRemove,
  isNew = false,
  isFlashing = false,
}: ViewportRowProps) {
  // The device type drives the capture's emulation, so a type that doesn't
  // follow the width was chosen on purpose: keep it when the width changes.
  const [manualDeviceType, setManualDeviceType] = useState(
    () => viewport.device_type !== detectDeviceType(viewport.width),
  )

  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  const errors = validateResolution(viewport.width, viewport.height)
  const errorText = [errors.width, errors.height].filter(Boolean).join(" · ")

  function update(patch: Partial<ViewportConfig>) {
    onChange(index, { ...viewport, ...patch })
  }

  function handleDeviceTypeChange(dt: DeviceType) {
    setManualDeviceType(true)
    update({ device_type: dt })
  }

  function handleWidthChange(w: number) {
    const patch: Partial<ViewportConfig> = { width: w }
    if (!manualDeviceType) patch.device_type = detectDeviceType(w)
    update(patch)
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "flex flex-col gap-1 px-3 py-2 transition-colors duration-300",
        isDragging ? "relative z-10 bg-card opacity-60 shadow-lg" : "hover:bg-muted/30",
        isFlashing && "bg-muted/40 ring-2 ring-inset ring-foreground/30",
        isNew && !isFlashing && "ring-1 ring-inset ring-foreground/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {/* Drag handle — a real button so keyboard reordering works too */}
        <Button
          ref={setActivatorNodeRef}
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={`Reorder viewport ${viewport.width}×${viewport.height}`}
          className="cursor-grab touch-none text-muted-foreground active:cursor-grabbing"
          {...attributes}
          {...listeners}
        >
          <GripVertical />
        </Button>

        <DeviceTypeToggle value={viewport.device_type} onChange={handleDeviceTypeChange} />

        <ResolutionInputs
          width={viewport.width}
          height={viewport.height}
          onWidthChange={handleWidthChange}
          onHeightChange={(h) => update({ height: h })}
        />

        {/* Browsers + remove wrap together on narrow cards */}
        <div className="flex min-w-fit flex-1 items-center justify-between gap-2">
          <BrowserMultiSelect
            selected={viewport.browsers}
            defaultBrowsers={defaultBrowsers}
            onChange={(browsers) => update({ browsers })}
          />

          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            onClick={() => onRemove(index)}
            disabled={disableRemove}
            aria-label="Remove viewport"
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 />
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
