import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import type { FieldErrors, UseFormRegister } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { KeyRound, Save } from "lucide-react"
import { settingsApi } from "@/api/settings"
import { extractApiError } from "@/lib/helpers"
import PageHeader from "@/components/layout/PageHeader"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { FormField } from "@/components/ui/FormField"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Slider } from "@/components/ui/slider"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const schema = z.object({
  // Capture
  screenshot_format: z.enum(["jpeg", "png"]),
  screenshot_quality: z.number().int().min(0).max(100),
  // Workers & performance
  sem_limit: z.number().int().min(1).max(64),
  task_job_timeout_seconds: z.number().int().min(1),
  reuse_browser_context: z.boolean(),
  // Security & sessions
  access_token_expire_minutes: z.number().int().min(1),
  password_reset_token_expire_minutes: z.number().int().min(1),
  password_reset_rate_limit_max: z.number().int().min(1),
  password_reset_rate_limit_window_seconds: z.number().int().min(1),
  // Email / SMTP
  smtp_host: z.string(),
  smtp_port: z.number().int().min(1).max(65535),
  smtp_from_email: z.string(),
  smtp_use_tls: z.boolean(),
  frontend_base_url: z.string(),
})

type FormValues = z.infer<typeof schema>

function NumberField({
  name,
  label,
  hint,
  register,
  errors,
}: {
  name: keyof FormValues
  label: string
  hint?: string
  register: UseFormRegister<FormValues>
  errors: FieldErrors<FormValues>
}) {
  const id = `setting-${name}`
  return (
    <FormField
      label={label}
      htmlFor={id}
      hint={hint}
      error={errors[name]?.message as string | undefined}
    >
      <Input
        id={id}
        type="number"
        className="max-w-xs"
        {...register(name, { valueAsNumber: true })}
      />
    </FormField>
  )
}

function SettingsSection({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>
        <FieldGroup>{children}</FieldGroup>
      </CardContent>
    </Card>
  )
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [quality, setQuality] = useState(85)

  const { data, isLoading } = useQuery({
    queryKey: ["application-settings"],
    queryFn: settingsApi.get,
  })

  const {
    handleSubmit,
    register,
    reset,
    setValue,
    watch,
    formState: { errors, isDirty },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      screenshot_format: "jpeg",
      screenshot_quality: 85,
      sem_limit: 4,
      task_job_timeout_seconds: 7200,
      reuse_browser_context: true,
      access_token_expire_minutes: 2880,
      password_reset_token_expire_minutes: 30,
      password_reset_rate_limit_max: 5,
      password_reset_rate_limit_window_seconds: 3600,
      smtp_host: "",
      smtp_port: 587,
      smtp_from_email: "no-reply@example.com",
      smtp_use_tls: true,
      frontend_base_url: "",
    },
  })

  const screenshotFormat = watch("screenshot_format")
  const smtpUseTls = watch("smtp_use_tls")
  const reuseBrowserContext = watch("reuse_browser_context")

  useEffect(() => {
    if (!data) return
    // smtp_host can be null when no .env default is configured.
    reset({ ...data, smtp_host: data.smtp_host ?? "" })
    setQuality(data.screenshot_quality)
  }, [data, reset])

  const updateMutation = useMutation({
    mutationFn: settingsApi.update,
    onSuccess: () => {
      toast.success("Settings saved.")
      void queryClient.invalidateQueries({ queryKey: ["application-settings"] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err)),
  })

  if (isLoading) {
    return (
      <div className="flex max-w-3xl flex-col gap-6">
        <Skeleton className="h-8 w-40" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-56 w-full" />
        ))}
      </div>
    )
  }

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Settings</h1>}
        description="Global application settings"
      />

      <form
        onSubmit={handleSubmit((values) => updateMutation.mutate(values))}
        className="flex flex-col gap-6"
      >
        <SettingsSection title="Capture" description="Image format used for new screenshots.">
          <FormField label="Screenshot format" htmlFor="setting-screenshot_format">
            <Select
              value={screenshotFormat}
              onValueChange={(value: FormValues["screenshot_format"]) =>
                setValue("screenshot_format", value, { shouldDirty: true })
              }
            >
              <SelectTrigger id="setting-screenshot_format" className="w-full max-w-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="jpeg">JPEG</SelectItem>
                <SelectItem value="png">PNG</SelectItem>
              </SelectContent>
            </Select>
          </FormField>

          {screenshotFormat === "jpeg" && (
            <Field className="max-w-xs">
              <div className="flex items-center justify-between">
                <FieldLabel htmlFor="setting-screenshot_quality">Screenshot quality</FieldLabel>
                <span className="text-sm font-medium tabular-nums">{quality}</span>
              </div>
              <Slider
                id="setting-screenshot_quality"
                min={0}
                max={100}
                step={1}
                value={[quality]}
                onValueChange={(values: number[]) => {
                  const next = values[0] ?? quality
                  setQuality(next)
                  setValue("screenshot_quality", next, { shouldDirty: true })
                }}
              />
            </Field>
          )}
        </SettingsSection>

        <SettingsSection
          title="Workers & performance"
          description="How many pages are captured at once and how long a task may run."
        >
          <NumberField
            name="sem_limit"
            label="Concurrent captures (SEM_LIMIT)"
            hint="Parallel screenshot captures per worker, shared by all running tasks and re-captures. Applies when the next task or re-capture starts."
            register={register}
            errors={errors}
          />
          <NumberField
            name="task_job_timeout_seconds"
            label="Task job timeout (seconds)"
            hint="Applies to new tasks. Raising it above the .env value still needs a worker restart (ARQ hard ceiling). Time spent waiting for a free capture slot counts too."
            register={register}
            errors={errors}
          />
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="setting-reuse_browser_context">
                Reuse browser context across viewports
              </FieldLabel>
              <FieldDescription>
                Captures all viewports of a URL in one browser through a shared context: a warm
                HTTP cache and fewer repeated downloads, but cookies and localStorage carry over
                between those viewports. Retries always use a fresh isolated context. Applies to
                new tasks.
              </FieldDescription>
            </FieldContent>
            <Switch
              id="setting-reuse_browser_context"
              checked={reuseBrowserContext}
              onCheckedChange={(checked: boolean) =>
                setValue("reuse_browser_context", checked, { shouldDirty: true })
              }
            />
          </Field>
        </SettingsSection>

        <SettingsSection title="Security & sessions" description="Session and password-reset limits.">
          <NumberField
            name="access_token_expire_minutes"
            label="Access token lifetime (minutes)"
            register={register}
            errors={errors}
          />
          <NumberField
            name="password_reset_token_expire_minutes"
            label="Password reset link lifetime (minutes)"
            register={register}
            errors={errors}
          />
          <NumberField
            name="password_reset_rate_limit_max"
            label="Password reset rate limit (max attempts)"
            register={register}
            errors={errors}
          />
          <NumberField
            name="password_reset_rate_limit_window_seconds"
            label="Password reset rate limit window (seconds)"
            register={register}
            errors={errors}
          />
        </SettingsSection>

        <SettingsSection title="Email / SMTP" description="Outgoing mail for password-reset links.">
          <Alert>
            <KeyRound />
            <AlertDescription>
              SMTP credentials (username &amp; password) are configured server-side in{" "}
              <code className="font-mono">.env</code> and are not editable here.
            </AlertDescription>
          </Alert>

          <FormField
            label="SMTP host"
            htmlFor="setting-smtp_host"
            hint="Leave empty to disable password-reset emails."
            error={errors.smtp_host?.message}
          >
            <Input
              id="setting-smtp_host"
              type="text"
              className="max-w-md"
              placeholder="smtp.example.com"
              {...register("smtp_host")}
            />
          </FormField>
          <NumberField name="smtp_port" label="SMTP port" register={register} errors={errors} />
          <FormField label="From email" htmlFor="setting-smtp_from_email" error={errors.smtp_from_email?.message}>
            <Input
              id="setting-smtp_from_email"
              type="text"
              className="max-w-md"
              placeholder="no-reply@example.com"
              {...register("smtp_from_email")}
            />
          </FormField>
          <FormField
            label="Frontend base URL"
            htmlFor="setting-frontend_base_url"
            hint="Used to build password-reset links in emails."
            error={errors.frontend_base_url?.message}
          >
            <Input
              id="setting-frontend_base_url"
              type="text"
              className="max-w-md"
              placeholder="https://app.example.com"
              {...register("frontend_base_url")}
            />
          </FormField>
          <Field orientation="horizontal">
            <FieldLabel htmlFor="setting-smtp_use_tls">Use TLS (STARTTLS)</FieldLabel>
            <Switch
              id="setting-smtp_use_tls"
              checked={smtpUseTls}
              onCheckedChange={(checked: boolean) =>
                setValue("smtp_use_tls", checked, { shouldDirty: true })
              }
            />
          </Field>
        </SettingsSection>

        {/* Sticks to the bottom of the viewport while the form scrolls */}
        <div className="sticky bottom-4 z-10 flex flex-col gap-3 rounded-xl border bg-card/95 px-4 py-3 shadow-sm backdrop-blur supports-backdrop-filter:bg-card/80 sm:flex-row sm:items-center sm:justify-between">
          {isDirty ? (
            <span className="flex items-center gap-2 text-sm font-medium">
              <span className="size-2 rounded-full bg-warning" />
              Unsaved changes
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">All settings are saved</span>
          )}
          <Button type="submit" disabled={updateMutation.isPending}>
            {updateMutation.isPending ? <Spinner data-icon="inline-start" /> : <Save data-icon="inline-start" />}
            {updateMutation.isPending ? "Saving…" : "Save settings"}
          </Button>
        </div>
      </form>
    </div>
  )
}
