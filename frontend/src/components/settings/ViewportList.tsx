import { useState, useCallback, useRef } from "react"
import { ChevronDown, Monitor, Plus, Smartphone, Tablet } from "lucide-react"
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ViewportRow } from "./ViewportRow"
import { DraftViewportRow } from "./DraftViewportRow"
import type { ViewportConfig } from "@/types"
import type { DeviceType } from "./DeviceTypeToggle"

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ViewportListProps {
  viewports: ViewportConfig[]
  onChange: (viewports: ViewportConfig[]) => void
  defaultBrowsers: string[]
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PRESETS: { width: number; height: number }[] = [
  { width: 1920, height: 1080 },
  { width: 1680, height: 1050 },
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 1024, height: 1366 },
  { width: 834, height: 1112 },
  { width: 768, height: 1024 },
  { width: 428, height: 926 },
  { width: 390, height: 844 },
  { width: 360, height: 780 },
]

function detectDeviceType(width: number): DeviceType {
  if (width >= 1024) return "desktop"
  if (width >= 768) return "tablet"
  return "mobile"
}

const DEVICE_ICONS = { desktop: Monitor, tablet: Tablet, mobile: Smartphone }

// ─── Component ────────────────────────────────────────────────────────────────

export function ViewportList({ viewports, onChange, defaultBrowsers }: ViewportListProps) {
  const [flashIdx, setFlashIdx] = useState<number | null>(null)
  const [newIdx, setNewIdx] = useState<number | null>(null)
  const [showDraft, setShowDraft] = useState(false)

  // Stable ids for dnd-kit — one per viewport position, generated once and
  // kept in sync with the viewports array length via a ref.
  const idCounterRef = useRef(0)
  const rowIdsRef = useRef<string[]>([])

  // Grow the id array when viewports are added
  while (rowIdsRef.current.length < viewports.length) {
    rowIdsRef.current.push(`vp-${idCounterRef.current++}`)
  }
  // Trim if viewports were removed
  rowIdsRef.current = rowIdsRef.current.slice(0, viewports.length)

  const rowIds = rowIdsRef.current

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = rowIds.indexOf(active.id as string)
    const newIndex = rowIds.indexOf(over.id as string)
    if (oldIndex === -1 || newIndex === -1) return

    // Reorder both the ids array and the viewports array together
    rowIdsRef.current = arrayMove(rowIds, oldIndex, newIndex)
    onChange(arrayMove(viewports, oldIndex, newIndex))
  }

  const commitViewport = useCallback(
    (viewport: ViewportConfig) => {
      const existingIdx = viewports.findIndex(
        (vp) => vp.width === viewport.width && vp.height === viewport.height,
      )
      if (existingIdx >= 0) {
        setShowDraft(false)
        setFlashIdx(existingIdx)
        setTimeout(() => setFlashIdx(null), 1500)
        return
      }
      const nextIdx = viewports.length
      onChange([...viewports, viewport])
      setShowDraft(false)
      setNewIdx(nextIdx)
      setTimeout(() => setNewIdx(null), 1500)
    },
    [viewports, onChange],
  )

  const addPreset = useCallback(
    (width: number, height: number) => {
      commitViewport({ width, height, device_type: detectDeviceType(width), browsers: null })
    },
    [commitViewport],
  )

  function handleChange(index: number, updated: ViewportConfig) {
    onChange(viewports.map((vp, i) => (i === index ? updated : vp)))
  }

  function handleRemove(index: number) {
    onChange(viewports.filter((_, i) => i !== index))
    rowIdsRef.current = rowIdsRef.current.filter((_, i) => i !== index)
    if (newIdx !== null && newIdx >= index) setNewIdx(Math.max(0, newIdx - 1))
    if (flashIdx !== null && flashIdx >= index) setFlashIdx(Math.max(0, flashIdx - 1))
  }

  return (
    <div className="divide-y overflow-hidden rounded-lg border">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={rowIds} strategy={verticalListSortingStrategy}>
          {viewports.map((vp, i) => (
            <ViewportRow
              key={rowIds[i]}
              id={rowIds[i]!}
              viewport={vp}
              index={i}
              onChange={handleChange}
              onRemove={handleRemove}
              defaultBrowsers={defaultBrowsers}
              disableRemove={viewports.length === 1 && !showDraft}
              isNew={newIdx === i}
              isFlashing={flashIdx === i}
            />
          ))}
        </SortableContext>
      </DndContext>

      {/* Draft row — blank inputs, no device type pre-selected */}
      {showDraft && (
        <DraftViewportRow
          defaultBrowsers={defaultBrowsers}
          onCommit={commitViewport}
          onDiscard={() => setShowDraft(false)}
        />
      )}

      {/* Add / preset row */}
      <div className="flex flex-wrap items-center gap-1 bg-muted/30 px-2 py-1.5">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setShowDraft(true)}
          disabled={showDraft}
        >
          <Plus data-icon="inline-start" />
          Add viewport
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="ghost" size="sm">
              Add preset
              <ChevronDown data-icon="inline-end" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-44">
            <DropdownMenuLabel>Common screen sizes</DropdownMenuLabel>
            {PRESETS.map((preset) => {
              const Icon = DEVICE_ICONS[detectDeviceType(preset.width)]
              return (
                <DropdownMenuItem
                  key={`${preset.width}x${preset.height}`}
                  onSelect={() => addPreset(preset.width, preset.height)}
                >
                  <Icon />
                  <span className="tabular-nums">
                    {preset.width} × {preset.height}
                  </span>
                </DropdownMenuItem>
              )
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
