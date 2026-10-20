import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import type { FieldErrors, UseFormRegister } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { settingsApi } from "@/api/settings"
import { extractApiError } from "@/lib/helpers"
import PageHeader from "@/components/layout/PageHeader"
import { Button } from "@/components/ui/button"
import { FormField } from "@/components/ui/FormField"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { SectionHeading } from "@/components/ui/SectionHeading"
import { Slider } from "@/components/ui/slider"
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
  return (
    <FormField
      label={label}
      hint={hint}
      error={errors[name]?.message as string | undefined}
    >
      <Input
        type="number"
        className="max-w-xs"
        {...register(name, { valueAsNumber: true })}
      />
    </FormField>
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
    formState: { errors },
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
    return <div className="text-muted-foreground">Loading settings…</div>
  }

  return (
    <div className="max-w-3xl">
      <PageHeader
        bordered={false}
        className="mb-6"
        title={<h1 className="text-2xl font-semibold">Settings</h1>}
        description="Global application settings"
      />

      <form
        onSubmit={handleSubmit((values) => updateMutation.mutate(values))}
        className="space-y-6"
      >
        {/* ─── Capture ─────────────────────────────────────────────── */}
        <div className="bg-card border border-border p-6 space-y-5">
          <SectionHeading title="Capture" />

          <FormField label="Screenshot format">
            <Select
              value={screenshotFormat}
              onValueChange={(value: FormValues["screenshot_format"]) =>
                setValue("screenshot_format", value)
              }
            >
              <SelectTrigger className="w-full max-w-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="jpeg">JPEG</SelectItem>
                <SelectItem value="png">PNG</SelectItem>
              </SelectContent>
            </Select>
          </FormField>

          {screenshotFormat === "jpeg" && (
            <div className="space-y-2 max-w-xs">
              <div className="flex items-center justify-between">
                <Label>Screenshot quality</Label>
                <span className="text-sm font-medium">{quality}</span>
              </div>
              <Slider
                min={0}
                max={100}
                step={1}
                value={[quality]}
                onValueChange={(values: number[]) => {
                  const next = values[0] ?? quality
                  setQuality(next)
                  setValue("screenshot_quality", next)
                }}
              />
            </div>
          )}
        </div>

        {/* ─── Workers & Performance ───────────────────────────────── */}
        <div className="bg-card border border-border p-6 space-y-5">
          <SectionHeading title="Workers & Performance" />

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
          <FormField hint="Captures all viewports of a URL in one browser through a shared context: a warm HTTP cache and fewer repeated downloads, but cookies and localStorage carry over between those viewports. Retries always use a fresh isolated context. Applies to new tasks.">
            <div className="flex items-center gap-3">
              <Switch
                checked={reuseBrowserContext}
                onCheckedChange={(checked: boolean) =>
                  setValue("reuse_browser_context", checked)
                }
              />
              <Label>Reuse browser context across viewports</Label>
            </div>
          </FormField>
        </div>

        {/* ─── Security & Sessions ─────────────────────────────────── */}
        <div className="bg-card border border-border p-6 space-y-5">
          <SectionHeading title="Security & Sessions" />

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
        </div>

        {/* ─── Email / SMTP ────────────────────────────────────────── */}
        <div className="bg-card border border-border p-6 space-y-5">
          <SectionHeading title="Email / SMTP" />
          <p className="text-xs text-muted-foreground -mt-2">
            SMTP credentials (username &amp; password) are configured server-side
            in <code>.env</code> and are not editable here.
          </p>

          <FormField
            label="SMTP host"
            hint="Leave empty to disable password-reset emails."
            error={errors.smtp_host?.message}
          >
            <Input
              type="text"
              className="max-w-md"
              placeholder="smtp.example.com"
              {...register("smtp_host")}
            />
          </FormField>
          <NumberField
            name="smtp_port"
            label="SMTP port"
            register={register}
            errors={errors}
          />
          <FormField label="From email" error={errors.smtp_from_email?.message}>
            <Input
              type="text"
              className="max-w-md"
              placeholder="no-reply@example.com"
              {...register("smtp_from_email")}
            />
          </FormField>
          <FormField
            label="Frontend base URL"
            hint="Used to build password-reset links in emails."
            error={errors.frontend_base_url?.message}
          >
            <Input
              type="text"
              className="max-w-md"
              placeholder="https://app.example.com"
              {...register("frontend_base_url")}
            />
          </FormField>
          <div className="flex items-center gap-3">
            <Switch
              checked={smtpUseTls}
              onCheckedChange={(checked: boolean) =>
                setValue("smtp_use_tls", checked)
              }
            />
            <Label>Use TLS (STARTTLS)</Label>
          </div>
        </div>

        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending ? "Saving…" : "Save settings"}
        </Button>
      </form>
    </div>
  )
}
