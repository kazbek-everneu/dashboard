import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react"
import { cn } from "@/lib/utils"
import { TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { ColumnDef } from "./DataTable"
import { CELL_PADDING, type Density } from "./density"

interface DataTableHeaderProps<T> {
  columns: ColumnDef<T>[]
  hasActions: boolean
  sort?: { key: string; direction: "asc" | "desc" }
  onSortChange?: (key: string, direction: "asc" | "desc") => void
  density?: Density
}

export function DataTableHeader<T,>({
  columns,
  hasActions,
  sort,
  onSortChange,
  density = "default",
}: DataTableHeaderProps<T>) {
  const handleSort = (key: string) => {
    if (!onSortChange) return
    if (sort?.key === key) {
      onSortChange(key, sort.direction === "asc" ? "desc" : "asc")
    } else {
      onSortChange(key, "desc")
    }
  }

  return (
    <TableHeader className="bg-muted/50">
      <TableRow className="hover:bg-transparent">
        {columns.map((col) => {
          const isSortable = !!col.sortKey && !!onSortChange
          const isActive = sort?.key === col.key
          const SortIcon = !isActive ? ArrowUpDown : sort?.direction === "asc" ? ArrowUp : ArrowDown

          return (
            <TableHead
              key={col.key}
              aria-sort={
                isActive ? (sort?.direction === "asc" ? "ascending" : "descending") : undefined
              }
              className={cn(
                "h-10 text-muted-foreground",
                CELL_PADDING[density],
                "py-0",
                col.hideOnMobile && "hidden sm:table-cell",
                col.width,
              )}
            >
              {isSortable ? (
                <button
                  type="button"
                  onClick={() => handleSort(col.key)}
                  className={cn(
                    "-mx-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-1 outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                    isActive && "text-foreground",
                  )}
                >
                  {col.header}
                  <SortIcon className={cn("size-3.5", !isActive && "opacity-50")} />
                </button>
              ) : (
                col.header
              )}
            </TableHead>
          )
        })}
        {hasActions && (
          <TableHead className={cn("w-14", CELL_PADDING[density], "py-0")}>
            <span className="sr-only">Actions</span>
          </TableHead>
        )}
      </TableRow>
    </TableHeader>
  )
}
