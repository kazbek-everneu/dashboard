import { BrowserMultiSelect } from "@/components/settings/BrowserMultiSelect"
import { ViewportList } from "@/components/settings/ViewportList"
import { Input } from "@/components/ui/input"
import { FormField } from "@/components/ui/FormField"
import { SectionHeading } from "@/components/ui/SectionHeading"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { ProjectConfig, ViewportConfig } from "@/types"

interface Props {
  isLoading: boolean
  defaultBrowsers: string[]
  viewports: ViewportConfig[]
  waitUntil: ProjectConfig["wait_until"]
  timeout: string
  timeoutError?: string
  onDefaultBrowsersChange: (browsers: string[]) => void
  onViewportsChange: (viewports: ViewportConfig[]) => void
  onWaitUntilChange: (value: ProjectConfig["wait_until"]) => void
  onTimeoutChange: (value: string) => void
}

export default function ProjectSettingsPage({
  isLoading,
  defaultBrowsers,
  viewports,
  waitUntil,
  timeout,
  timeoutError,
  onDefaultBrowsersChange,
  onViewportsChange,
  onWaitUntilChange,
  onTimeoutChange,
}: Props) {
  if (isLoading) return <div className="py-6 text-sm text-muted-foreground">Loading settings…</div>

  return (
    <div className="w-full space-y-0">
      <div className="mb-1">
        <SectionHeading title="Default Browser" />
        <div className="glass-card p-6 space-y-2">
          <p className="text-sm text-muted-foreground">
            The browsers used for all viewports unless overridden per viewport. Select one or more.
          </p>
          <BrowserMultiSelect
            selected={defaultBrowsers}
            defaultBrowsers={defaultBrowsers}
            onChange={(browsers) => onDefaultBrowsersChange(browsers ?? defaultBrowsers)}
          />
        </div>
      </div>

      <div className="mt-6 mb-1">
        <SectionHeading title="Viewports & Browsers" />
        <div className="glass-card p-6 space-y-3">
          <p className="text-sm text-muted-foreground">Configure screen sizes and browsers to capture.</p>
          <ViewportList
            viewports={viewports}
            onChange={onViewportsChange}
            defaultBrowsers={defaultBrowsers}
          />
          {viewports.length === 0 && (
            <p className="text-sm text-destructive">At least one viewport is required.</p>
          )}
        </div>
      </div>

      <div className="mt-6 mb-1">
        <SectionHeading title="Page Behaviour" />
        <div className="glass-card p-6 space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Wait until">
              <Select value={waitUntil} onValueChange={onWaitUntilChange}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="load">load</SelectItem>
                  <SelectItem value="domcontentloaded">domcontentloaded</SelectItem>
                  <SelectItem value="networkidle">networkidle</SelectItem>
                </SelectContent>
              </Select>
            </FormField>

            <FormField
              label="Timeout (ms)"
              htmlFor="timeout"
              error={timeoutError}
            >
              <Input
                id="timeout"
                type="number"
                value={timeout}
                onChange={(event) => onTimeoutChange(event.target.value)}
              />
            </FormField>
          </div>
        </div>
      </div>
    </div>
  )
}
