import React from "react"
import { cn } from "@/lib/utils"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import { StateMessage } from "@/components/ui/StateMessage"
import { DataTableHeader } from "./DataTableHeader"
import { DataTableRow } from "./DataTableRow"
import { DataTableSkeleton } from "./DataTableSkeleton"
import { DataTablePagination } from "./DataTablePagination"
import type { Density } from "./density"

export interface RowAction<T> {
  label: string
  icon?: React.ReactNode
  onClick: (row: T) => void
  variant?: "default" | "destructive"
}

export interface ColumnDef<T> {
  /** Unique key for this column */
  key: string
  /** Header label shown in thead */
  header: string
  /** Tailwind width class e.g. "w-[200px]" */
  width?: string
  /** Render cell content */
  cell: (row: T) => React.ReactNode
  /** If provided, column is sortable */
  sortKey?: keyof T
  /** Hide on mobile screens */
  hideOnMobile?: boolean
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[]
  data: T[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  rowActions?: (row: T) => RowAction<T>[]
  isLoading?: boolean
  emptyState?: {
    icon?: React.ReactNode
    title: string
    description?: string
    action?: React.ReactNode
  }
  pagination?: {
    page: number
    pageSize: number
    total: number
    onPageChange: (page: number) => void
  }
  sort?: {
    key: string
    direction: "asc" | "desc"
  }
  onSortChange?: (key: string, direction: "asc" | "desc") => void
  /** Rows always shown at the top before regular rows */
  pinnedRows?: T[]
  className?: string
  density?: Density
}

export function DataTable<T,>({
  columns,
  data,
  rowKey,
  onRowClick,
  rowActions,
  isLoading = false,
  emptyState,
  pagination,
  sort,
  onSortChange,
  pinnedRows,
  className,
  density = "default",
}: DataTableProps<T>) {
  const hasActions = !!rowActions
  const colSpan = columns.length + (hasActions ? 1 : 0)

  // Deduplicate: remove pinned rows from regular data to avoid duplicates
  const pinnedKeys = new Set((pinnedRows ?? []).map(rowKey))
  const regularRows = pinnedRows?.length ? data.filter((r) => !pinnedKeys.has(rowKey(r))) : data
  const allRows = [...(pinnedRows ?? []), ...regularRows]

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="overflow-hidden rounded-xl border bg-card">
        <Table>
          <DataTableHeader
            columns={columns}
            hasActions={hasActions}
            sort={sort}
            onSortChange={onSortChange}
            density={density}
          />
          <TableBody>
            {isLoading ? (
              <DataTableSkeleton columns={columns} hasActions={hasActions} density={density} />
            ) : allRows.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={colSpan} className="p-0 whitespace-normal">
                  {emptyState ? (
                    <StateMessage
                      icon={emptyState.icon}
                      title={emptyState.title}
                      description={emptyState.description}
                      action={emptyState.action}
                      className="border-0"
                    />
                  ) : (
                    <StateMessage title="No data" compact className="border-0" />
                  )}
                </TableCell>
              </TableRow>
            ) : (
              allRows.map((row) => (
                <DataTableRow
                  key={rowKey(row)}
                  row={row}
                  columns={columns}
                  onRowClick={onRowClick}
                  actions={rowActions?.(row)}
                  isPinned={pinnedKeys.has(rowKey(row))}
                  density={density}
                />
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {pagination && pagination.total > pagination.pageSize && (
        <DataTablePagination {...pagination} />
      )}
    </div>
  )
}
