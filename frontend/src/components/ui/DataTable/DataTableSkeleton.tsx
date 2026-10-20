import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { TableCell, TableRow } from "@/components/ui/table"
import type { ColumnDef } from "./DataTable"
import { CELL_PADDING, type Density } from "./density"

// Widths cycle per column position for visual variety
const SKELETON_WIDTHS = ["w-2/3", "w-4/5", "w-1/2", "w-3/5", "w-3/4", "w-1/3"]

interface DataTableSkeletonProps<T> {
  columns: ColumnDef<T>[]
  hasActions: boolean
  rowCount?: number
  density?: Density
}

export function DataTableSkeleton<T,>({
  columns,
  hasActions,
  rowCount = 5,
  density = "default",
}: DataTableSkeletonProps<T>) {
  return (
    <>
      {Array.from({ length: rowCount }).map((_, rowIdx) => (
        <TableRow key={rowIdx} className="hover:bg-transparent">
          {columns.map((col, colIdx) => (
            <TableCell
              key={col.key}
              className={cn(CELL_PADDING[density], col.hideOnMobile && "hidden sm:table-cell")}
            >
              <Skeleton
                className={cn("h-4", SKELETON_WIDTHS[(rowIdx + colIdx) % SKELETON_WIDTHS.length])}
              />
              {/* Second line for "name + subtext" columns */}
              {colIdx === 0 && <Skeleton className="mt-1.5 h-3 w-1/3" />}
            </TableCell>
          ))}
          {hasActions && (
            <TableCell className={CELL_PADDING[density]}>
              <Skeleton className="ml-auto size-7" />
            </TableCell>
          )}
        </TableRow>
      ))}
    </>
  )
}
