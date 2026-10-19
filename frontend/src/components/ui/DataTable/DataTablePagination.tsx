import { Button } from "@/components/ui/button"

interface DataTablePaginationProps {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
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

  const pageNumbers = totalPages <= 7 ? Array.from({ length: totalPages }, (_, i) => i + 1) : null

  return (
    <div className="flex items-center justify-between text-sm text-muted-foreground">
      <span>
        Showing {from}–{to} of {total}
      </span>

      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page === 1}
        >
          Previous
        </Button>

        {pageNumbers && (
          <div className="flex items-center gap-1 mx-1">
            {pageNumbers.map((n) => (
              <Button
                key={n}
                variant={n === page ? "default" : "ghost"}
                size="sm"
                className="w-8 h-8 p-0"
                onClick={() => onPageChange(n)}
              >
                {n}
              </Button>
            ))}
          </div>
        )}

        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page === totalPages}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
