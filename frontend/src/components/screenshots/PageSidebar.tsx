import { cn } from "@/lib/utils"
import type { PageItem } from "@/hooks/useScreenshotFilters"

interface Props {
  pages: PageItem[]
  totalCount: number
  activePage: string | null
  onSelect: (url: string | null) => void
}

export function PageSidebar({ pages, totalCount, activePage, onSelect }: Props) {
  return (
    // A short scrolling list above the gallery on phones, a column beside it on desktop.
    <aside className="max-h-56 w-full shrink-0 overflow-y-auto border-b bg-muted/30 [scrollbar-width:thin] md:max-h-none md:w-60 md:border-r md:border-b-0">
      <nav aria-label="Pages" className="flex flex-col gap-0.5 p-2">
        <SidebarItem
          label="All pages"
          count={totalCount}
          isActive={activePage === null}
          onClick={() => onSelect(null)}
        />
        {pages.map((page) => (
          <SidebarItem
            key={page.url}
            label={page.label}
            fullUrl={page.url}
            count={page.count}
            hasFailed={page.hasFailed}
            isActive={activePage === page.url}
            onClick={() => onSelect(page.url)}
          />
        ))}
      </nav>
    </aside>
  )
}

function SidebarItem({
  label,
  fullUrl,
  count,
  hasFailed = false,
  isActive,
  onClick,
}: {
  label: string
  fullUrl?: string
  count: number
  hasFailed?: boolean
  isActive: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      title={fullUrl}
      onClick={onClick}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50",
        isActive
          ? "bg-background font-medium text-foreground shadow-xs ring-1 ring-border"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        {hasFailed && (
          <span className="size-1.5 shrink-0 rounded-full bg-destructive" aria-label="has failures" />
        )}
        <span className="truncate">{label}</span>
      </span>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{count}</span>
    </button>
  )
}
