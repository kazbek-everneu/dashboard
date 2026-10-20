import { Link } from "react-router-dom"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"
import { EverneuMark, EverneuWordmark } from "./Logo"
import NavMain from "./NavMain"
import NavUser from "./NavUser"

export default function AppSidebar() {
  const { state, isMobile, setOpenMobile } = useSidebar()
  // The mobile sheet is always shown expanded.
  const collapsed = state === "collapsed" && !isMobile

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-14 justify-center">
        <Link
          to="/projects"
          aria-label="everneu — Projects"
          onClick={() => isMobile && setOpenMobile(false)}
          className={cn(
            "flex h-8 items-center rounded-md px-2 text-sidebar-foreground outline-hidden focus-visible:ring-2 focus-visible:ring-sidebar-ring",
            collapsed && "justify-center px-0",
          )}
        >
          {collapsed ? (
            <EverneuMark className="h-5 w-auto" />
          ) : (
            <EverneuWordmark className="h-5 w-auto" />
          )}
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <NavMain />
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
