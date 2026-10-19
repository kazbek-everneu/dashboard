import { useMemo } from "react"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { UrlSelector } from "@/components/sitemap/UrlSelector"
import type { SitemapURL } from "@/types"

interface Props {
  apiUrls: SitemapURL[]
  urls: SitemapURL[]
  selectedIds: Set<string>
  onSelectionChange: (ids: Set<string>) => void
  onAddManualUrl: (url: string) => void
  onDeleteManualUrl: (urlId: string) => void
  onFetchSitemap: () => void
  isLoading: boolean
  isFetching: boolean
}

function relativeTime(dateStr: string): string {
  const diffMs = Date.now() - new Date(dateStr).getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return "just now"
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

export default function SitemapTab({
  apiUrls,
  urls,
  selectedIds,
  onSelectionChange,
  onAddManualUrl,
  onDeleteManualUrl,
  onFetchSitemap,
  isLoading,
  isFetching,
}: Props) {
  const lastFetched = useMemo(() => {
    const times = apiUrls.map((u) => u.last_fetched_at).filter(Boolean) as string[]
    if (!times.length) return null
    return times.reduce((latest, t) => (t > latest ? t : latest), times[0])
  }, [apiUrls])

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Button
          variant="outline"
          size="sm"
          onClick={onFetchSitemap}
          disabled={isFetching}
        >
          <RefreshCw
            className={`h-4 w-4 mr-1.5 ${isFetching ? "animate-spin" : ""}`}
          />
          {isFetching ? "Fetching…" : "Fetch Sitemap"}
        </Button>
        {lastFetched && (
          <span
            className="text-xs text-muted-foreground"
            title={new Date(lastFetched).toLocaleString()}
          >
            Last fetched: {relativeTime(lastFetched)}
          </span>
        )}
      </div>

      <UrlSelector
        urls={urls}
        selectedIds={selectedIds}
        onSelectionChange={onSelectionChange}
        showManualInput
        onAddManualUrl={onAddManualUrl}
        onDeleteManualUrl={onDeleteManualUrl}
        isLoading={isLoading}
        emptyMessage='No URLs yet. Click "Fetch Sitemap" to import from WordPress, or add URLs manually below.'
      />
    </div>
  )
}
