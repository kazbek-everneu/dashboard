import { Separator } from "@/components/ui/separator"

interface SectionHeadingProps {
  title: string
}

export function SectionHeading({ title }: SectionHeadingProps) {
  return (
    <div className="mb-3 flex items-center gap-3">
      <h3 className="shrink-0 text-sm font-medium text-foreground">{title}</h3>
      <Separator className="flex-1" />
    </div>
  )
}
