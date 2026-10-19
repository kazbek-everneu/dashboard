import type { ReactNode } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

interface AuthCardLayoutProps {
  title: ReactNode
  description: ReactNode
  children: ReactNode
  footer?: ReactNode
}

export function AuthCardLayout({
  title,
  description,
  children,
  footer,
}: AuthCardLayoutProps) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>
          {children}
          {footer}
        </CardContent>
      </Card>
    </div>
  )
}
