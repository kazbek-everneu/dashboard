import { useNavigate } from "react-router-dom"
import { ChevronsUpDown, LogOut } from "lucide-react"

import { authApi } from "@/api/auth"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { useAuth } from "@/hooks/useAuth"

export default function NavUser() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const { isMobile } = useSidebar()

  const handleLogout = async () => {
    try {
      await authApi.logout()
    } catch {
      // Clear local auth state even if the network request fails.
    }
    logout()
    navigate("/login", { replace: true })
  }

  const name = user?.name || user?.email || ""
  const initials = (user?.name || user?.email || "QA").slice(0, 2).toUpperCase()

  const identity = (
    <>
      <Avatar className="size-8 rounded-lg">
        <AvatarFallback className="rounded-lg text-xs font-semibold">{initials}</AvatarFallback>
      </Avatar>
      <div className="grid flex-1 text-left text-sm leading-tight">
        <span className="truncate font-medium">{name}</span>
        <span className="truncate text-xs text-muted-foreground">{user?.email}</span>
      </div>
    </>
  )

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              {identity}
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="p-0 font-normal text-foreground">
              <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                {identity}
                {user?.is_admin && <Badge variant="secondary">Admin</Badge>}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={handleLogout}>
              <LogOut />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
