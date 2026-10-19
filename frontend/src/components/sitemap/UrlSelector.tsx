import { useState, useMemo } from "react"
import { Search, Trash2, ChevronDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import type { SitemapURL } from "@/types"

export interface UrlSelectorProps {
  // Data
  urls: SitemapURL[]

  // Selection state
  selectedIds: Set<string>
  onSelectionChange: (ids: Set<string>) => void

  // Manual URL management — only shown when enabled
  showManualInput?: boolean
  onAddManualUrl?: (url: string) => void
  onDeleteManualUrl?: (urlId: string) => void

  // Display
  title?: string
  isLoading?: boolean
  emptyMessage?: string
}

function groupByCpt(urls: SitemapURL[]): Record<string, SitemapURL[]> {
  return urls.reduce<Record<string, SitemapURL[]>>((acc, u) => {
    acc[u.cpt] = acc[u.cpt] ? [...acc[u.cpt], u] : [u]
    return acc
  }, {})
}

export function UrlSelector({
  urls,
  selectedIds,
  onSelectionChange,
  showManualInput = false,
  onAddManualUrl,
  onDeleteManualUrl,
  title = "Select pages to screenshot",
  isLoading = false,
  emptyMessage = "No pages found.",
}: UrlSelectorProps) {
  const [filter, setFilter] = useState("")
  const [manualInput, setManualInput] = useState("")
  const [manualError, setManualError] = useState<string | null>(null)
  const [openCpts, setOpenCpts] = useState<Record<string, boolean>>({})

  const filteredUrls = useMemo(() => {
    if (!filter) return urls
    const q = filter.toLowerCase()
    return urls.filter((u) => u.url.toLowerCase().includes(q))
  }, [urls, filter])

  const grouped = groupByCpt(filteredUrls)

  const toggleUrl = (id: string, checked: boolean) => {
    const next = new Set(selectedIds)
    if (checked) next.add(id)
    else next.delete(id)
    onSelectionChange(next)
  }

  const toggleGroup = (ids: string[], allChecked: boolean) => {
    const next = new Set(selectedIds)
    if (!allChecked) ids.forEach((id) => next.add(id))
    else ids.forEach((id) => next.delete(id))
    onSelectionChange(next)
  }

  const selectAll = () => onSelectionChange(new Set(urls.map((u) => u.id)))
  const deselectAll = () => onSelectionChange(new Set())

  const isCptOpen = (cpt: string) => openCpts[cpt] !== false
  const toggleCpt = (cpt: string) =>
    setOpenCpts((prev) => ({ ...prev, [cpt]: !isCptOpen(cpt) }))

  const handleAddManualUrl = () => {
    const url = manualInput.trim()
    if (!url) return
    if (!url.startsWith("/") && !url.startsWith("http://") && !url.startsWith("https://")) {
      setManualError("URL must start with / or https://")
      return
    }
    if (urls.some((u) => u.url === url)) {
      setManualError("URL already added")
      return
    }
    setManualError(null)
    setManualInput("")
    onAddManualUrl?.(url)
  }

  return (
    <div className="border overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b bg-muted/30 space-y-3">
        <div className="flex items-center justify-between">
          <p className="font-medium text-sm">{title}</p>
          <span className="text-xs text-muted-foreground">{selectedIds.size} selected</span>
        </div>
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={selectAll}
            disabled={urls.length === 0}
            className="text-xs h-7 px-2"
          >
            Select all
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={deselectAll}
            disabled={selectedIds.size === 0}
            className="text-xs h-7 px-2"
          >
            Deselect all
          </Button>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
          <Input
            placeholder="Filter pages…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="h-8 text-sm pl-8"
          />
        </div>
      </div>

      {/* URL list body */}
      <div className="flex-1 overflow-y-auto p-2 min-h-[300px] max-h-[500px]">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            Loading pages…
          </div>
        ) : urls.length === 0 ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground text-center px-4">
            {emptyMessage}
          </div>
        ) : Object.keys(grouped).length === 0 ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            No pages match your filter.
          </div>
        ) : (
          <div className="space-y-1">
            {Object.entries(grouped).map(([cpt, cptUrls]) => {
              const ids = cptUrls.map((u) => u.id)
              const allChecked = ids.every((id) => selectedIds.has(id))
              const someChecked = ids.some((id) => selectedIds.has(id))
              const isOpen = isCptOpen(cpt)

              return (
                <Collapsible key={cpt} open={isOpen} onOpenChange={() => toggleCpt(cpt)}>
                  <div className="flex items-center gap-2 px-1 py-1.5 hover:bg-muted/50">
                    <Checkbox
                      checked={allChecked ? true : someChecked ? "indeterminate" : false}
                      onCheckedChange={() => toggleGroup(ids, allChecked)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <CollapsibleTrigger className="flex items-center gap-2 flex-1 text-left">
                      <span className="font-medium text-sm capitalize">{cpt}</span>
                      <span className="text-xs text-muted-foreground">({cptUrls.length})</span>
                      <ChevronDown
                        className={`h-3 w-3 ml-auto text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`}
                      />
                    </CollapsibleTrigger>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-xs h-6 px-2 shrink-0"
                      onClick={() => toggleGroup(ids, allChecked)}
                    >
                      {allChecked ? "Deselect" : "Select"} all
                    </Button>
                  </div>
                  <CollapsibleContent>
                    <div className="pl-7 space-y-0.5 pb-1">
                      {cptUrls.map((u) => (
                        <div
                          key={u.id}
                          className="flex items-center gap-2 px-1 py-0.5 hover:bg-muted/50"
                        >
                          <Checkbox
                            checked={selectedIds.has(u.id)}
                            onCheckedChange={(checked) => toggleUrl(u.id, checked === true)}
                          />
                          <span className="text-xs truncate flex-1">{u.url}</span>
                          {showManualInput && u.cpt === "manual" && onDeleteManualUrl && (
                            <button
                              type="button"
                              onClick={() => onDeleteManualUrl(u.id)}
                              className="p-0.5 text-muted-foreground transition-colors shrink-0 hover:bg-destructive/10 hover:text-destructive"
                              title="Remove URL"
                            >
                              <Trash2 className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              )
            })}
          </div>
        )}
      </div>

      {/* Manual URL input */}
      {showManualInput && (
        <div className="border-t px-4 py-3 space-y-2 bg-muted/10">
          <p className="text-xs font-medium text-muted-foreground">+ Add URL manually</p>
          <div className="flex gap-2">
            <Input
              type="text"
              placeholder="https://example.com/page/"
              value={manualInput}
              onChange={(e) => {
                setManualInput(e.target.value)
                setManualError(null)
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault()
                  handleAddManualUrl()
                }
              }}
              className="h-8 text-sm flex-1"
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleAddManualUrl}
              disabled={!manualInput.trim()}
              className="h-8 shrink-0"
            >
              Add
            </Button>
          </div>
          {manualError && <p className="text-xs text-destructive">{manualError}</p>}
        </div>
      )}
    </div>
  )
}
