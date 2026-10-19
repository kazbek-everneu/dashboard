import { Monitor, Tablet, Smartphone } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

export type DeviceType = "desktop" | "tablet" | "mobile"

export interface DeviceTypeToggleProps {
  /** null = no device type selected (draft state) */
  value: DeviceType | null
  onChange: (v: DeviceType) => void
}

const DEVICES: { value: DeviceType; Icon: LucideIcon }[] = [
  { value: "desktop", Icon: Monitor },
  { value: "tablet", Icon: Tablet },
  { value: "mobile", Icon: Smartphone },
]

export function DeviceTypeToggle({ value, onChange }: DeviceTypeToggleProps) {
  return (
    <div className="flex shrink-0 overflow-hidden border border-border divide-x divide-border">
      {DEVICES.map(({ value: dv, Icon }) => (
        <button
          key={dv}
          type="button"
          onClick={() => onChange(dv)}
          className={cn(
            "p-1.5 transition-colors",
            value === dv
              ? "bg-foreground text-background"
              : "bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <Icon className="size-5" />
        </button>
      ))}
    </div>
  )
}
