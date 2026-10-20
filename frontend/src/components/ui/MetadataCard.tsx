import type { ReactNode } from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface MetadataCardProps {
  label: string
  children: ReactNode
  className?: string
}

export function MetadataCard({ label, children, className }: MetadataCardProps) {
  return (
    <Card size="sm" className={className}>
      <CardHeader>
        <CardTitle className="text-xs font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">{children}</CardContent>
    </Card>
  )
}
