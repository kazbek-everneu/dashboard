import { Input } from "@/components/ui/input"
import { RESOLUTION_INPUT_CLASS, validateResolution } from "./resolution"

export interface ResolutionInputsProps {
  width: number
  height: number
  onWidthChange: (w: number) => void
  onHeightChange: (h: number) => void
}

export function ResolutionInputs({
  width,
  height,
  onWidthChange,
  onHeightChange,
}: ResolutionInputsProps) {
  const errors = validateResolution(width, height)

  return (
    <div className="flex shrink-0 items-center gap-1">
      <Input
        type="number"
        value={width}
        min={320}
        max={3840}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10)
          if (!isNaN(v)) onWidthChange(v)
        }}
        aria-label="Width (px)"
        aria-invalid={!!errors.width || undefined}
        className={RESOLUTION_INPUT_CLASS}
      />
      <span className="select-none text-xs text-muted-foreground">×</span>
      <Input
        type="number"
        value={height}
        min={400}
        max={2160}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10)
          if (!isNaN(v)) onHeightChange(v)
        }}
        aria-label="Height (px)"
        aria-invalid={!!errors.height || undefined}
        className={RESOLUTION_INPUT_CLASS}
      />
    </div>
  )
}
