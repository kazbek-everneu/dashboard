import { useState } from "react"
import { Outlet } from "react-router-dom"
import Sidebar from "./Sidebar"
import TopBar from "./TopBar"
import { useLayoutStore } from "@/store/layout"
import { cn } from "@/lib/utils"

export default function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const collapsed = useLayoutStore((s) => s.collapsed)

  return (
    <div className="h-screen overflow-hidden bg-background">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div
        className={cn(
          "flex h-full flex-col transition-[margin] duration-300 ease-in-out",
          collapsed ? "lg:ml-[96px]" : "lg:ml-[280px]",
        )}
      >
        <TopBar onMenuClick={() => setMobileOpen(true)} />
        <main className="flex-1 overflow-y-auto px-4 pb-6 pt-4 lg:pb-8 lg:pt-6">
          <div className="page-shell mx-auto w-full">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
