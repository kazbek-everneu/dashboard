import { BrowserMultiSelect } from "@/components/settings/BrowserMultiSelect"
import { ViewportList } from "@/components/settings/ViewportList"
import { FormField } from "@/components/ui/FormField"
import {
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSeparator,
  FieldSet,
} from "@/components/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import type { ProjectConfig, ViewportConfig } from "@/types"

interface Props {
  isLoading: boolean
  /** Show the configuration without letting the viewer change it. */
  readOnly?: boolean
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
  readOnly = false,
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
  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  return (
    // A disabled fieldset disables every control inside it, nested ones included.
    <fieldset disabled={readOnly} className="contents">
      <FieldGroup>
        <FieldSet>
          <FieldLegend>Default browsers</FieldLegend>
          <FieldDescription>
            Used for every viewport unless the viewport overrides them. Select one or more.
          </FieldDescription>
          <BrowserMultiSelect
            selected={defaultBrowsers}
            defaultBrowsers={defaultBrowsers}
            onChange={(browsers) => onDefaultBrowsersChange(browsers ?? defaultBrowsers)}
          />
        </FieldSet>

        <FieldSeparator />

        <FieldSet>
          <FieldLegend>Viewports &amp; browsers</FieldLegend>
          <FieldDescription>Screen sizes to capture. Drag rows to reorder.</FieldDescription>
          <ViewportList
            viewports={viewports}
            onChange={onViewportsChange}
            defaultBrowsers={defaultBrowsers}
          />
          {viewports.length === 0 && <FieldError>At least one viewport is required.</FieldError>}
        </FieldSet>

        <FieldSeparator />

        <FieldSet>
          <FieldLegend>Page behaviour</FieldLegend>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Wait until" htmlFor="wait-until">
              <Select value={waitUntil} onValueChange={onWaitUntilChange}>
                <SelectTrigger id="wait-until" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="load">load</SelectItem>
                  <SelectItem value="domcontentloaded">domcontentloaded</SelectItem>
                  <SelectItem value="networkidle">networkidle</SelectItem>
                </SelectContent>
              </Select>
            </FormField>

            <FormField label="Timeout" htmlFor="timeout" error={timeoutError}>
              <InputGroup>
                <InputGroupInput
                  id="timeout"
                  type="number"
                  value={timeout}
                  aria-invalid={!!timeoutError || undefined}
                  onChange={(event) => onTimeoutChange(event.target.value)}
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText>ms</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
            </FormField>
          </div>
        </FieldSet>
      </FieldGroup>
    </fieldset>
  )
}
