import { Fragment } from "react"
import { MoreHorizontal } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { TableCell, TableRow } from "@/components/ui/table"
import type { ColumnDef, RowAction } from "./DataTable"
import { CELL_PADDING, type Density } from "./density"

interface DataTableRowProps<T> {
  row: T
  columns: ColumnDef<T>[]
  onRowClick?: (row: T) => void
  actions?: RowAction<T>[]
  isPinned?: boolean
  density?: Density
}

export function DataTableRow<T,>({
  row,
  columns,
  onRowClick,
  actions,
  isPinned = false,
  density = "default",
}: DataTableRowProps<T>) {
  const clickable = !!onRowClick

  return (
    <TableRow
      data-pinned={isPinned || undefined}
      tabIndex={clickable ? 0 : undefined}
      className={cn(
        clickable && "cursor-pointer outline-none focus-visible:bg-muted/50",
        isPinned && "bg-muted/40",
      )}
      onClick={clickable ? () => onRowClick(row) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              // Only the row itself; keys inside the actions menu must not navigate.
              if (e.target !== e.currentTarget) return
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault()
                onRowClick(row)
              }
            }
          : undefined
      }
    >
      {columns.map((col, idx) => (
        <TableCell
          key={col.key}
          className={cn(
            "whitespace-normal",
            CELL_PADDING[density],
            col.hideOnMobile && "hidden sm:table-cell",
            // Left accent on the first cell of pinned rows
            idx === 0 && isPinned && "border-l-2 border-l-foreground/30",
          )}
        >
          {col.cell(row)}
        </TableCell>
      ))}

      {actions && (
        <TableCell
          className={cn("w-14 text-right", CELL_PADDING[density])}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Non-modal so an action can open a dialog without leaving the page inert. */}
          {actions.length > 0 && (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Open row actions"
                  className="text-muted-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground"
                >
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-40">
                {actions.map((action, i) => (
                  <Fragment key={action.label}>
                    {action.variant === "destructive" && i > 0 && <DropdownMenuSeparator />}
                    <DropdownMenuItem
                      variant={action.variant ?? "default"}
                      onSelect={() => action.onClick(row)}
                    >
                      {action.icon}
                      {action.label}
                    </DropdownMenuItem>
                  </Fragment>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </TableCell>
      )}
    </TableRow>
  )
}
