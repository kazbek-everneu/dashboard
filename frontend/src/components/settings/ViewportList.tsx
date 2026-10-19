import { useState, useCallback, useRef } from "react"
import { Plus } from "lucide-react"
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
    <div className="border border-border overflow-hidden divide-y divide-border/50">
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
      <div className="flex items-center gap-2 py-2 px-3 bg-muted/20">
        <button
          type="button"
          onClick={() => setShowDraft(true)}
          disabled={showDraft}
          className="flex items-center text-sm text-muted-foreground hover:text-foreground transition-colors shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Plus className="size-3.5 mr-1" />
          Add viewport
        </button>
        <span className="text-xs text-muted-foreground shrink-0">or pick:</span>
        <div className="flex items-center gap-0 flex-wrap">
          {PRESETS.map((preset, i) => (
            <span key={i} className="flex items-center">
              <button
                type="button"
                onClick={() => addPreset(preset.width, preset.height)}
                className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline transition-colors px-1"
              >
                {preset.width}×{preset.height}
              </button>
              {i < PRESETS.length - 1 && (
                <span className="text-xs text-muted-foreground/50 select-none">·</span>
              )}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
