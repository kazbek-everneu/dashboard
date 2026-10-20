import type { ReactNode } from "react"

import { EverneuWordmark } from "@/components/layout/Logo"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

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
    <div className="flex min-h-svh flex-col items-center justify-center bg-muted p-6 md:p-10">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <EverneuWordmark className="h-6 w-auto self-center text-foreground" />
        <Card>
          <CardHeader className="text-center">
            <CardTitle className="text-xl">{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent>{children}</CardContent>
          {footer ? (
            <CardFooter className="justify-center text-center text-sm text-muted-foreground">
              {/* A block wrapper keeps inline spacing (the footer itself is flex). */}
              <p>{footer}</p>
            </CardFooter>
          ) : null}
        </Card>
      </div>
    </div>
  )
}
