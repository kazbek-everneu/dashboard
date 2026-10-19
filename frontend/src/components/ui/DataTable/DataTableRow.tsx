import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { MoreHorizontal } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ColumnDef, RowAction } from "./DataTable"

const MENU_WIDTH = 192
const MENU_GAP = 8
const VIEWPORT_PADDING = 8

interface MenuPosition {
  top: number
  left: number
}

interface DataTableRowProps<T> {
  row: T
  columns: ColumnDef<T>[]
  onRowClick?: (row: T) => void
  actions?: RowAction<T>[]
  isPinned?: boolean
  isLast?: boolean
  density?: "default" | "compact"
}

export function DataTableRow<T,>({
  row,
  columns,
  onRowClick,
  actions,
  isPinned = false,
  isLast = false,
  density = "default",
}: DataTableRowProps<T>) {
  const [dropOpen, setDropOpen] = useState(false)
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const closeMenu = useCallback(() => {
    setDropOpen(false)
    setMenuPosition(null)
  }, [])

  const updateMenuPosition = useCallback(() => {
    const button = buttonRef.current
    if (!button) return

    const buttonRect = button.getBoundingClientRect()
    const menuRect = menuRef.current?.getBoundingClientRect()
    const menuWidth = menuRect?.width || MENU_WIDTH
    const menuHeight = menuRect?.height || 0
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    const maxLeft = Math.max(VIEWPORT_PADDING, viewportWidth - menuWidth - VIEWPORT_PADDING)
    const maxTop = Math.max(VIEWPORT_PADDING, viewportHeight - menuHeight - VIEWPORT_PADDING)

    let left = buttonRect.right - menuWidth
    let top = buttonRect.bottom + MENU_GAP

    const spaceBelow = viewportHeight - buttonRect.bottom - MENU_GAP - VIEWPORT_PADDING
    const spaceAbove = buttonRect.top - MENU_GAP - VIEWPORT_PADDING
    if (menuHeight > 0 && spaceBelow < menuHeight && spaceAbove > spaceBelow) {
      top = buttonRect.top - menuHeight - MENU_GAP
    }

    left = Math.min(Math.max(left, VIEWPORT_PADDING), maxLeft)
    top = Math.min(Math.max(top, VIEWPORT_PADDING), maxTop)

    setMenuPosition({ top, left })
  }, [])

  useLayoutEffect(() => {
    if (!dropOpen) return
    updateMenuPosition()
  }, [dropOpen, updateMenuPosition])

  useEffect(() => {
    if (!dropOpen) return

    window.addEventListener("resize", updateMenuPosition)
    window.addEventListener("scroll", updateMenuPosition, true)
    return () => {
      window.removeEventListener("resize", updateMenuPosition)
      window.removeEventListener("scroll", updateMenuPosition, true)
    }
  }, [dropOpen, updateMenuPosition])

  return (
    <tr
      className={cn(
        "transition-colors duration-150",
        !isLast && "border-b surface-divider",
        onRowClick && "cursor-pointer hover:bg-muted/60",
        isPinned && "bg-muted/40",
      )}
      onClick={onRowClick ? () => onRowClick(row) : undefined}
    >
      {columns.map((col, idx) => (
        <td
          key={col.key}
          className={cn(
            col.hideOnMobile ? "hidden sm:table-cell" : "",
            density === "compact" ? "px-4 py-3" : "px-5 py-4",
            // Left border on first cell of pinned rows
            idx === 0 && isPinned && "border-l-2 border-foreground/20",
          )}
        >
          {col.cell(row)}
        </td>
      ))}

      {actions && (
        <td
          className={cn(density === "compact" ? "px-4 py-3" : "px-5 py-4", "text-right w-[80px]")}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="inline-block">
            <button
              ref={buttonRef}
              type="button"
              aria-haspopup="menu"
              aria-expanded={dropOpen}
              aria-label="Open row actions"
              onClick={() => {
                if (dropOpen) {
                  closeMenu()
                } else {
                  setMenuPosition(null)
                  setDropOpen(true)
                }
              }}
              className="p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>

            {dropOpen &&
              createPortal(
                <>
                  <div className="fixed inset-0 z-40" onClick={closeMenu} />
                  <div
                    ref={menuRef}
                    role="menu"
                    className="glass-elevated fixed z-50 w-48 py-1.5"
                    style={{
                      top: menuPosition?.top ?? 0,
                      left: menuPosition?.left ?? 0,
                      visibility: menuPosition ? "visible" : "hidden",
                    }}
                  >
                    {actions.map((action, i) => (
                      <button
                        key={i}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          closeMenu()
                          action.onClick(row)
                        }}
                        className={cn(
                          "flex items-center gap-2 w-full px-4 py-2.5 text-sm transition-colors",
                          action.variant === "destructive"
                            ? "text-destructive hover:bg-destructive/10"
                            : "hover:bg-muted",
                        )}
                      >
                        {action.icon && (
                          <span className="shrink-0">{action.icon}</span>
                        )}
                        {action.label}
                      </button>
                    ))}
                  </div>
                </>,
                document.body,
              )}
          </div>
        </td>
      )}
    </tr>
  )
}
