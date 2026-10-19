import type { ColumnDef } from "./DataTable"

// Widths cycle per column position for visual variety
const SKELETON_WIDTHS = ["w-2/3", "w-4/5", "w-1/2", "w-3/5", "w-3/4", "w-1/3"]

interface DataTableSkeletonProps<T> {
  columns: ColumnDef<T>[]
  hasActions: boolean
  rowCount?: number
}

export function DataTableSkeleton<T,>({
  columns,
  hasActions,
  rowCount = 5,
}: DataTableSkeletonProps<T>) {
  return (
    <>
      {Array.from({ length: rowCount }).map((_, rowIdx) => (
        <tr key={rowIdx} className="border-b border-border/50 last:border-0">
          {columns.map((col, colIdx) => (
            <td
              key={col.key}
              className={col.hideOnMobile ? "hidden sm:table-cell px-4 py-3" : "px-4 py-3"}
            >
              <div
                className={`h-4 bg-muted animate-pulse ${SKELETON_WIDTHS[(rowIdx + colIdx) % SKELETON_WIDTHS.length]}`}
              />
              {/* Second line for "name + subtext" columns */}
              {colIdx === 0 && (
                <div className="mt-1.5 h-3 w-1/3 bg-muted animate-pulse opacity-60" />
              )}
            </td>
          ))}
          {hasActions && (
            <td className="px-4 py-3 text-right">
              <div className="ml-auto h-7 w-7 bg-muted animate-pulse" />
            </td>
          )}
        </tr>
      ))}
    </>
  )
}
