import { useState } from "react"
import { GripVertical, Trash2 } from "lucide-react"
import { useSortable } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { cn } from "@/lib/utils"
import { DeviceTypeToggle } from "./DeviceTypeToggle"
import { ResolutionInputs } from "./ResolutionInputs"
import { BrowserMultiSelect } from "./BrowserMultiSelect"
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
  const [manualDeviceType, setManualDeviceType] = useState(false)

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

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
      {...attributes}
      className={cn(
        "group flex items-center gap-3 py-2 px-3 transition-all duration-300",
        isDragging
          ? "opacity-50 bg-card shadow-lg z-10"
          : "hover:bg-muted/30",
        isFlashing && "ring-2 ring-inset ring-foreground/30 bg-muted/20",
        isNew && !isFlashing && "ring-1 ring-inset ring-foreground/20",
      )}
    >
      {/* Col 0 — drag handle */}
      <GripVertical
        {...listeners}
        size={14}
        className="text-muted-foreground/40 group-hover:text-muted-foreground cursor-grab active:cursor-grabbing shrink-0 touch-none"
      />

      {/* Col 1 — device type */}
      <DeviceTypeToggle value={viewport.device_type} onChange={handleDeviceTypeChange} />

      {/* Col 2 — resolution */}
      <ResolutionInputs
        width={viewport.width}
        height={viewport.height}
        onWidthChange={handleWidthChange}
        onHeightChange={(h) => update({ height: h })}
      />

      {/* Col 3 — browser icons */}
      <BrowserMultiSelect
        selected={viewport.browsers}
        defaultBrowsers={defaultBrowsers}
        onChange={(browsers) => update({ browsers })}
        className="flex-1"
      />

      {/* Col 4 — remove */}
      <button
        type="button"
        onClick={() => onRemove(index)}
        disabled={disableRemove}
        className={cn(
          "ml-auto shrink-0 p-1.5 transition-colors",
          disableRemove
            ? "text-muted-foreground/30 cursor-not-allowed"
            : "text-muted-foreground hover:text-destructive",
        )}
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  )
}
