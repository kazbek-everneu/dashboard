import { useMemo, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Controller, useForm, useWatch } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import { Check, ChevronsUpDown, Save } from "lucide-react"
import { usersApi } from "@/api/users"
import { useAuth } from "@/hooks/useAuth"
import { browserTimeZone, formatDateTimeWithZone, supportedTimeZones } from "@/lib/datetime"
import { extractApiError } from "@/lib/helpers"
import PageHeader from "@/components/layout/PageHeader"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { FieldGroup } from "@/components/ui/field"
import { FormField } from "@/components/ui/FormField"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import type { User } from "@/types"

const AUTO = "__auto__"

const profileSchema = z.object({
  name: z.string().trim().min(1, "Name is required"),
  // AUTO = follow the browser (stored as null).
  timezone: z.string(),
})
type ProfileValues = z.infer<typeof profileSchema>

function TimeZonePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const zones = useMemo(() => supportedTimeZones(), [])
  const detected = browserTimeZone()
  const label = value === AUTO ? `Auto (${detected})` : value

  const choose = (next: string) => {
    onChange(next)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id="profile-timezone"
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full max-w-sm justify-between font-normal"
        >
          <span className="truncate">{label}</span>
          <ChevronsUpDown data-icon="inline-end" className="text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) p-0">
        <Command>
          <CommandInput placeholder="Search time zones…" />
          <CommandList>
            <CommandEmpty>No time zone matches.</CommandEmpty>
            <CommandGroup>
              <CommandItem value={`auto ${detected}`} onSelect={() => choose(AUTO)}>
                <Check className={cn(value === AUTO ? "opacity-100" : "opacity-0")} />
                Auto — follow this browser ({detected})
              </CommandItem>
            </CommandGroup>
            <CommandSeparator />
            <CommandGroup>
              {zones.map((zone) => (
                <CommandItem key={zone} value={zone} onSelect={() => choose(zone)}>
                  <Check className={cn(value === zone ? "opacity-100" : "opacity-0")} />
                  {zone}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

export default function ProfilePage() {
  const { user, setUser } = useAuth()
  const queryClient = useQueryClient()

  const form = useForm<ProfileValues>({
    resolver: zodResolver(profileSchema),
    values: user ? { name: user.name, timezone: user.timezone ?? AUTO } : undefined,
  })

  const mutation = useMutation({
    mutationFn: (values: ProfileValues) =>
      usersApi.update(user!.id, {
        name: values.name,
        timezone: values.timezone === AUTO ? null : values.timezone,
      }),
    onSuccess: (updated: User) => {
      setUser(updated)
      queryClient.setQueryData(["auth", "me"], updated)
      toast.success("Profile saved.")
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  const selectedZone = useWatch({ control: form.control, name: "timezone" })

  if (!user) return null

  const previewZone = selectedZone === AUTO ? browserTimeZone() : selectedZone

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Profile</h1>}
        description="Your name and how times are shown to you"
      />

      <form
        onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        className="flex flex-col gap-6"
      >
        <Card>
          <CardHeader className="border-b">
            <CardTitle>Account</CardTitle>
            <CardDescription>Your email is managed by an administrator.</CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <FormField
                label="Name"
                htmlFor="profile-name"
                error={form.formState.errors.name?.message}
              >
                <Input id="profile-name" className="max-w-sm" {...form.register("name")} />
              </FormField>
              <FormField label="Email" htmlFor="profile-email">
                <Input id="profile-email" className="max-w-sm" value={user.email} readOnly disabled />
              </FormField>
            </FieldGroup>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="border-b">
            <CardTitle>Time zone</CardTitle>
            <CardDescription>
              Dates and times across the app are shown in this time zone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <FormField
                label="Time zone"
                htmlFor="profile-timezone"
                hint={`Now: ${formatDateTimeWithZone(new Date().toISOString(), previewZone)}`}
              >
                <Controller
                  control={form.control}
                  name="timezone"
                  render={({ field }) => (
                    <TimeZonePicker
                      value={field.value}
                      onChange={(next) => field.onChange(next)}
                    />
                  )}
                />
              </FormField>
            </FieldGroup>
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <Button type="submit" disabled={mutation.isPending || !form.formState.isDirty}>
            {mutation.isPending ? <Spinner data-icon="inline-start" /> : <Save data-icon="inline-start" />}
            {mutation.isPending ? "Saving…" : "Save profile"}
          </Button>
        </div>
      </form>
    </div>
  )
}
