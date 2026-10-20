import { ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
} from "@/components/ui/pagination"

interface DataTablePaginationProps {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}

/** First, last and the current page's neighbours; gaps collapse to an ellipsis. */
function getPageItems(page: number, totalPages: number): (number | "ellipsis")[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)

  const items: (number | "ellipsis")[] = [1]
  const start = Math.max(2, page - 1)
  const end = Math.min(totalPages - 1, page + 1)
  if (start > 2) items.push("ellipsis")
  for (let n = start; n <= end; n++) items.push(n)
  if (end < totalPages - 1) items.push("ellipsis")
  items.push(totalPages)
  return items
}

export function DataTablePagination({
  page,
  pageSize,
  total,
  onPageChange,
}: DataTablePaginationProps) {
  const totalPages = Math.ceil(total / pageSize)
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)

  return (
    <div className="flex flex-col items-center gap-2 text-sm text-muted-foreground sm:flex-row sm:justify-between">
      <span>
        Showing {from}–{to} of {total}
      </span>

      {/* Buttons, not links: pages are client state, not URLs. */}
      <Pagination className="mx-0 w-auto">
        <PaginationContent>
          <PaginationItem>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onPageChange(page - 1)}
              disabled={page === 1}
            >
              <ChevronLeft data-icon="inline-start" />
              Previous
            </Button>
          </PaginationItem>

          {getPageItems(page, totalPages).map((item, i) =>
            item === "ellipsis" ? (
              <PaginationItem key={`ellipsis-${i}`}>
                <PaginationEllipsis />
              </PaginationItem>
            ) : (
              <PaginationItem key={item}>
                <Button
                  variant={item === page ? "outline" : "ghost"}
                  size="icon-sm"
                  aria-current={item === page ? "page" : undefined}
                  onClick={() => onPageChange(item)}
                >
                  {item}
                </Button>
              </PaginationItem>
            ),
          )}

          <PaginationItem>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onPageChange(page + 1)}
              disabled={page === totalPages}
            >
              Next
              <ChevronRight data-icon="inline-end" />
            </Button>
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  )
}
