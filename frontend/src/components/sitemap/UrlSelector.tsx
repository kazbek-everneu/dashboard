import { useState, useMemo } from "react"
import { Search, Trash2, ChevronDown, Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { FieldError } from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Skeleton } from "@/components/ui/skeleton"
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
    <div className="flex flex-col overflow-hidden rounded-lg border">
      {/* Header */}
      <div className="flex flex-col gap-2 border-b bg-muted/30 p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-medium">{title}</p>
          <Badge variant="secondary" className="tabular-nums">
            {selectedIds.size} selected
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <InputGroup className="flex-1 bg-background">
            <InputGroupAddon>
              <Search />
            </InputGroupAddon>
            <InputGroupInput
              placeholder="Filter pages…"
              aria-label="Filter pages"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </InputGroup>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={selectAll}
            disabled={urls.length === 0}
          >
            Select all
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={deselectAll}
            disabled={selectedIds.size === 0}
          >
            Clear
          </Button>
        </div>
      </div>

      {/* URL list body */}
      <div className="max-h-[500px] min-h-[300px] flex-1 overflow-y-auto p-2 [scrollbar-width:thin]">
        {isLoading ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-5" style={{ width: `${55 + ((i * 17) % 40)}%` }} />
            ))}
          </div>
        ) : urls.length === 0 ? (
          <div className="flex items-center justify-center px-4 py-16 text-center text-sm text-muted-foreground">
            {emptyMessage}
          </div>
        ) : Object.keys(grouped).length === 0 ? (
          <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
            No pages match your filter.
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {Object.entries(grouped).map(([cpt, cptUrls]) => {
              const ids = cptUrls.map((u) => u.id)
              const allChecked = ids.every((id) => selectedIds.has(id))
              const someChecked = ids.some((id) => selectedIds.has(id))
              const isOpen = isCptOpen(cpt)

              return (
                <Collapsible key={cpt} open={isOpen} onOpenChange={() => toggleCpt(cpt)}>
                  <div className="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-muted/50">
                    <Checkbox
                      checked={allChecked ? true : someChecked ? "indeterminate" : false}
                      onCheckedChange={() => toggleGroup(ids, allChecked)}
                      aria-label={`Select all ${cpt} pages`}
                    />
                    <CollapsibleTrigger className="flex flex-1 items-center gap-2 rounded-sm text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
                      <span className="text-sm font-medium capitalize">{cpt}</span>
                      <Badge variant="outline" className="tabular-nums">
                        {cptUrls.length}
                      </Badge>
                      <ChevronDown
                        className={cn(
                          "ml-auto size-3.5 text-muted-foreground transition-transform",
                          isOpen && "rotate-180",
                        )}
                      />
                    </CollapsibleTrigger>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      className="shrink-0 text-muted-foreground"
                      onClick={() => toggleGroup(ids, allChecked)}
                    >
                      {allChecked ? "Deselect" : "Select"} all
                    </Button>
                  </div>
                  <CollapsibleContent>
                    <div className="flex flex-col pb-1 pl-6">
                      {cptUrls.map((u) => (
                        <div
                          key={u.id}
                          className="flex items-center gap-2 rounded-md px-2 py-0.5 hover:bg-muted/50"
                        >
                          {/* The label makes the URL text toggle the checkbox too */}
                          <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 py-0.5">
                            <Checkbox
                              checked={selectedIds.has(u.id)}
                              onCheckedChange={(checked) => toggleUrl(u.id, checked === true)}
                            />
                            <span className="truncate text-xs" title={u.url}>
                              {u.url}
                            </span>
                          </label>
                          {showManualInput && u.cpt === "manual" && onDeleteManualUrl && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => onDeleteManualUrl(u.id)}
                              aria-label={`Remove ${u.url}`}
                              className="text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 />
                            </Button>
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
        <div className="flex flex-col gap-2 border-t bg-muted/30 p-3">
          <InputGroup className="bg-background">
            <InputGroupInput
              type="text"
              placeholder="Add a URL manually, e.g. https://example.com/page/"
              aria-label="Add URL manually"
              aria-invalid={!!manualError || undefined}
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
            />
            <InputGroupAddon align="inline-end">
              <InputGroupButton
                type="button"
                variant="secondary"
                onClick={handleAddManualUrl}
                disabled={!manualInput.trim()}
              >
                <Plus />
                Add
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
          {manualError && <FieldError>{manualError}</FieldError>}
        </div>
      )}
    </div>
  )
}
