import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react"
import type { ColumnDef } from "./DataTable"

interface DataTableHeaderProps<T> {
  columns: ColumnDef<T>[]
  hasActions: boolean
  sort?: { key: string; direction: "asc" | "desc" }
  onSortChange?: (key: string, direction: "asc" | "desc") => void
  density?: "default" | "compact"
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
    <thead className="sticky-table-head border-b surface-divider">
      <tr>
        {columns.map((col) => {
          const isSortable = !!col.sortKey && !!onSortChange
          const isActive = sort?.key === col.key

          return (
            <th
              key={col.key}
              className={[
                col.hideOnMobile ? "hidden sm:table-cell" : "",
                col.width ?? "",
                density === "compact" ? "px-4 py-3" : "px-5 py-4",
                "text-left text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground",
                isSortable ? "cursor-pointer select-none hover:text-foreground" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={isSortable ? () => handleSort(col.key) : undefined}
            >
              <span className="inline-flex items-center gap-1">
                {col.header}
                {isSortable && (
                  <>
                    {!isActive && (
                      <ChevronsUpDown className="h-3 w-3 opacity-50" />
                    )}
                    {isActive && sort?.direction === "asc" && (
                      <ChevronUp className="h-3 w-3" />
                    )}
                    {isActive && sort?.direction === "desc" && (
                      <ChevronDown className="h-3 w-3" />
                    )}
                  </>
                )}
              </span>
            </th>
          )
        })}
        {hasActions && <th className={density === "compact" ? "w-[80px] px-4 py-3" : "w-[88px] px-5 py-4"} />}
      </tr>
    </thead>
  )
}
