import { NavLink, useLocation } from "react-router-dom"
import { FolderOpen, ListTodo, Settings, Users, type LucideIcon } from "lucide-react"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { useAuth } from "@/hooks/useAuth"

type NavItem = {
  to: string
  label: string
  icon: LucideIcon
  adminOnly?: boolean
}

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { to: "/projects", label: "Projects", icon: FolderOpen },
      { to: "/tasks", label: "Tasks", icon: ListTodo },
    ],
  },
  {
    label: "Administration",
    items: [
      { to: "/users", label: "Users", icon: Users, adminOnly: true },
      { to: "/settings", label: "Global Settings", icon: Settings, adminOnly: true },
    ],
  },
]

export default function NavMain() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()

  return (
    <>
      {NAV_GROUPS.map((group) => {
        const items = group.items.filter((item) => !item.adminOnly || user?.is_admin)
        if (items.length === 0) return null

        return (
          <SidebarGroup key={group.label}>
            {/* In icon mode the hidden label slides over the previous group's
                last item (-mt-8); let clicks pass through to it. */}
            <SidebarGroupLabel className="group-data-[collapsible=icon]:pointer-events-none">
              {group.label}
            </SidebarGroupLabel>
            <SidebarMenu>
              {items.map(({ to, label, icon: Icon }) => (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton
                    asChild
                    tooltip={label}
                    isActive={pathname === to || pathname.startsWith(`${to}/`)}
                  >
                    <NavLink
                      to={to}
                      // The mobile sidebar is a sheet; close it after navigating.
                      onClick={() => isMobile && setOpenMobile(false)}
                    >
                      <Icon />
                      <span>{label}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        )
      })}
    </>
  )
}
