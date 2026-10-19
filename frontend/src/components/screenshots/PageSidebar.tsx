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
    <aside className="w-[240px] shrink-0 overflow-y-auto border-r surface-divider bg-[var(--surface-2)]">
      <nav className="py-2">
        <SidebarItem
          label="All"
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
      className={cn(
        "flex w-full items-center justify-between gap-2 border-y border-transparent px-4 py-2.5 text-left text-sm transition-colors",
        isActive
          ? "bg-background font-medium text-foreground border-border"
          : "text-[color:var(--text-secondary)] hover:bg-muted hover:text-foreground",
      )}
    >
      <span className="flex items-center gap-1.5 min-w-0">
        {hasFailed && (
          <span
            className="h-1.5 w-1.5 shrink-0 bg-destructive"
            aria-label="has failures"
          />
        )}
        <span className="truncate">{label}</span>
      </span>
      <span className="text-xs text-muted-foreground shrink-0">{count}</span>
    </button>
  )
}
