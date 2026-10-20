import { Outlet } from "react-router-dom"

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { useLayoutStore } from "@/store/layout"
import AppSidebar from "./AppSidebar"
import SiteHeader from "./SiteHeader"

export default function AppLayout() {
  const collapsed = useLayoutStore((s) => s.collapsed)
  const setCollapsed = useLayoutStore((s) => s.setCollapsed)

  return (
    <SidebarProvider open={!collapsed} onOpenChange={(open) => setCollapsed(!open)}>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <SiteHeader />
        <div className="flex flex-1 flex-col gap-6 p-4 md:p-6">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
