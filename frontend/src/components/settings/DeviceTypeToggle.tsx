import { Monitor, Tablet, Smartphone } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

export type DeviceType = "desktop" | "tablet" | "mobile"

export interface DeviceTypeToggleProps {
  /** null = no device type selected (draft state) */
  value: DeviceType | null
  onChange: (v: DeviceType) => void
}

const DEVICES: { value: DeviceType; label: string; Icon: LucideIcon }[] = [
  { value: "desktop", label: "Desktop", Icon: Monitor },
  { value: "tablet", label: "Tablet", Icon: Tablet },
  { value: "mobile", label: "Mobile", Icon: Smartphone },
]

export function DeviceTypeToggle({ value, onChange }: DeviceTypeToggleProps) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      spacing={0}
      value={value ?? ""}
      // Clicking the active item reports "" — a device type stays selected.
      onValueChange={(next) => next && onChange(next as DeviceType)}
      aria-label="Device type"
      className="shrink-0"
    >
      {DEVICES.map(({ value: dv, label, Icon }) => (
        <ToggleGroupItem
          key={dv}
          value={dv}
          aria-label={label}
          title={label}
          className="data-[state=on]:bg-foreground data-[state=on]:text-background"
        >
          <Icon />
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
