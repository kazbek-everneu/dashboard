import { cn } from "@/lib/utils"

export interface ResolutionInputsProps {
  width: number
  height: number
  onWidthChange: (w: number) => void
  onHeightChange: (h: number) => void
}

const NO_SPINNER =
  "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"

const BASE =
  "h-8 w-[5ch] border bg-background px-1 text-center text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-ring"

export function ResolutionInputs({
  width,
  height,
  onWidthChange,
  onHeightChange,
}: ResolutionInputsProps) {
  const widthInvalid = !isNaN(width) && (width < 320 || width > 3840)
  const heightInvalid = !isNaN(height) && (height < 400 || height > 2160)

  return (
    <div className="flex items-center shrink-0 gap-1">
      <input
        type="number"
        value={width}
        min={320}
        max={3840}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10)
          if (!isNaN(v)) onWidthChange(v)
        }}
        title={widthInvalid ? "Width must be 320–3840" : "Width (px)"}
        className={cn(BASE, NO_SPINNER, widthInvalid ? "ring-1 ring-destructive border-destructive" : "border-border")}
      />
      <span className="text-xs text-muted-foreground select-none">×</span>
      <input
        type="number"
        value={height}
        min={400}
        max={2160}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10)
          if (!isNaN(v)) onHeightChange(v)
        }}
        title={heightInvalid ? "Height must be 400–2160" : "Height (px)"}
        className={cn(BASE, NO_SPINNER, heightInvalid ? "ring-1 ring-destructive border-destructive" : "border-border")}
      />
    </div>
  )
}
