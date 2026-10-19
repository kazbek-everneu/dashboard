import { useState, useRef, useEffect } from "react"
import { useNavigate, useLocation } from "react-router-dom"
import { Menu, ChevronDown, LogOut } from "lucide-react"
import { authApi } from "@/api/auth"
import { useAuth } from "@/hooks/useAuth"
import { ThemeToggle } from "@/components/ui/ThemeToggle"

interface TopBarProps {
  onMenuClick: () => void
}

function buildBreadcrumb(pathname: string): string[] {
  const parts = pathname.split("/").filter(Boolean)
  const crumbs: string[] = ["Home"]

  if (parts[0] === "projects") {
    crumbs.push("Projects")
    if (parts[1]) crumbs.push("Detail")
  } else if (parts[0] === "tasks") {
    crumbs.push("Tasks")
    if (parts[1] === "new") crumbs.push("New Task")
    else if (parts[1]) crumbs.push("Detail")
  } else if (parts[0] === "users") {
    crumbs.push("Users")
  }

  return crumbs
}

export default function TopBar({ onMenuClick }: TopBarProps) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [dropOpen, setDropOpen] = useState(false)
  const dropRef = useRef<HTMLDivElement>(null)

  const crumbs = buildBreadcrumb(location.pathname)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setDropOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  const handleLogout = async () => {
    try {
      await authApi.logout()
    } catch {
      // Clear local auth state even if the network request fails.
    }
    logout()
    navigate("/login", { replace: true })
  }

  const initials = (user?.name || user?.email || "QA").slice(0, 2).toUpperCase()

  return (
    <header className="sticky top-0 z-30 shrink-0 px-4 pt-3 lg:pt-4">
      <div className="glass-card flex h-[68px] items-center justify-between border px-4 lg:px-6">
      {/* Left: hamburger + breadcrumb */}
      <div className="flex items-center gap-3">
        <button
          onClick={onMenuClick}
          className="lg:hidden p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Menu className="h-5 w-5" />
        </button>

        <nav className="hidden sm:flex items-center gap-2 text-sm">
          {crumbs.map((crumb, i) => (
            <span key={i} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-muted-foreground/40">/</span>}
              <span
                className={
                  i === crumbs.length - 1
                    ? "font-medium text-foreground"
                    : "text-[color:var(--text-secondary)]"
                }
              >
                {crumb}
              </span>
            </span>
          ))}
        </nav>
      </div>

      {/* Right: theme toggle + user dropdown */}
      <div className="flex items-center gap-2">
        <ThemeToggle />

        

        <div className="relative" ref={dropRef}>
          <button
            onClick={() => setDropOpen((v) => !v)}
            className="flex items-center gap-2 border border-transparent py-1.5 pl-2 pr-1.5 transition-colors hover:border-border hover:bg-muted"
          >
            <div className="flex h-9 w-9 items-center justify-center border bg-secondary">
              <span className="text-xs font-semibold text-primary">{initials}</span>
            </div>
            <span className="hidden md:block text-sm text-muted-foreground max-w-[160px] truncate">
              {user?.email ?? ""}
            </span>
            <ChevronDown
              className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${dropOpen ? "rotate-180" : ""}`}
            />
          </button>

          {dropOpen && (
            <div className="glass-elevated absolute right-0 top-full z-50 mt-2 w-60 py-1.5">
              <div className="border-b surface-divider px-4 py-3">
                <p className="text-xs font-medium text-foreground truncate">
                  {user?.name ?? user?.email ?? ""}
                </p>
                <p className="text-xs text-muted-foreground mt-0.5 truncate">
                  {user?.email ?? "Signed in"}
                </p>
              </div>
              <button
                onClick={handleLogout}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-destructive transition-colors hover:bg-destructive/10"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
      </div>
    </header>
  )
}
