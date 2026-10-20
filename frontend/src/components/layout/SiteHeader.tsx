import { Fragment } from "react"
import { Link, useLocation } from "react-router-dom"

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"
import { useBreadcrumbStore, type Crumb } from "@/store/breadcrumbs"
import ModeToggle from "./ModeToggle"

// Trail derived from the URL, shown until (or unless) the page names itself
// through useBreadcrumbs().
function crumbsFromPath(pathname: string): Crumb[] {
  const [section, id, sub] = pathname.split("/").filter(Boolean)

  if (section === "projects") {
    return id ? [{ label: "Projects", to: "/projects" }, { label: "Project" }] : [{ label: "Projects" }]
  }
  if (section === "tasks") {
    if (!id) return [{ label: "Tasks" }]
    if (id === "new") return [{ label: "Tasks", to: "/tasks" }, { label: "New task" }]
    if (sub === "screenshots") {
      return [
        { label: "Tasks", to: "/tasks" },
        { label: "Task", to: `/tasks/${id}` },
        { label: "Screenshot" },
      ]
    }
    return [{ label: "Tasks", to: "/tasks" }, { label: "Task" }]
  }
  if (section === "users") return [{ label: "Users" }]
  if (section === "settings") return [{ label: "Global Settings" }]
  return []
}

export default function SiteHeader() {
  const { pathname } = useLocation()
  const stored = useBreadcrumbStore()
  const crumbs = stored.pathname === pathname ? stored.crumbs : crumbsFromPath(pathname)

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur supports-backdrop-filter:bg-background/80">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 data-vertical:h-4 data-vertical:self-auto" />
      <Breadcrumb className="min-w-0 flex-1">
        <BreadcrumbList className="flex-nowrap">
          {crumbs.map((crumb, index) => {
            const isLast = index === crumbs.length - 1
            return (
              <Fragment key={`${index}-${crumb.label}`}>
                {index > 0 && <BreadcrumbSeparator className="hidden md:block" />}
                {/* Small screens only show the current page. */}
                <BreadcrumbItem className={cn("min-w-0", !isLast && "hidden md:inline-flex")}>
                  {isLast || !crumb.to ? (
                    <BreadcrumbPage className="truncate">{crumb.label}</BreadcrumbPage>
                  ) : (
                    <BreadcrumbLink asChild>
                      <Link to={crumb.to} className="truncate">
                        {crumb.label}
                      </Link>
                    </BreadcrumbLink>
                  )}
                </BreadcrumbItem>
              </Fragment>
            )
          })}
        </BreadcrumbList>
      </Breadcrumb>
      <div className="ml-auto flex items-center gap-1">
        <ModeToggle />
      </div>
    </header>
  )
}
