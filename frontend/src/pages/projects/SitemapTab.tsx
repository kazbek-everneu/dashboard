import { useMemo } from "react"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { DateTime } from "@/components/ui/DateTime"
import { Spinner } from "@/components/ui/spinner"
import { UrlSelector } from "@/components/sitemap/UrlSelector"
import { apiDateMs } from "@/lib/datetime"
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
    return times.reduce((latest, t) => (apiDateMs(t) > apiDateMs(latest) ? t : latest), times[0])
  }, [apiUrls])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="outline" size="sm" onClick={onFetchSitemap} disabled={isFetching}>
          {isFetching ? <Spinner data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
          {isFetching ? "Fetching…" : "Fetch sitemap"}
        </Button>
        {lastFetched && (
          <span className="text-xs text-muted-foreground">
            Last fetched <DateTime value={lastFetched} relative />
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
