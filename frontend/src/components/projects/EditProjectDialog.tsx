import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { projectsApi } from "@/api/projects"
import { extractApiError } from "@/lib/helpers"
import { Input } from "@/components/ui/input"
import { FormField } from "@/components/ui/FormField"
import { FormDialogFooter } from "@/components/ui/FormDialogFooter"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { Project } from "@/types"

const editProjectSchema = z.object({
  name: z.string().min(1, "Name is required"),
  url: z.string().url("Enter a valid URL (e.g. https://example.com)"),
})
type EditProjectValues = z.infer<typeof editProjectSchema>

interface EditProjectDialogProps {
  /** The project being edited; the dialog is open while it is set. */
  project: Pick<Project, "id" | "name" | "url"> | null
  onClose: () => void
}

export function EditProjectDialog({ project, onClose }: EditProjectDialogProps) {
  const queryClient = useQueryClient()

  const form = useForm<EditProjectValues>({
    resolver: zodResolver(editProjectSchema),
    values: project ? { name: project.name, url: project.url } : undefined,
  })

  const mutation = useMutation({
    mutationFn: (values: EditProjectValues) => projectsApi.update(project!.id, values),
    onSuccess: () => {
      toast.success("Project updated.")
      // ["projects"] also covers the ["projects", "summary"] query.
      void queryClient.invalidateQueries({ queryKey: ["projects"] })
      void queryClient.invalidateQueries({ queryKey: ["project", project?.id] })
      onClose()
    },
    onError: (err) => toast.error(extractApiError(err)),
  })

  return (
    <Dialog
      open={!!project}
      onOpenChange={(open) => {
        if (open) return
        onClose()
        form.reset()
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit project</DialogTitle>
          <DialogDescription>Update the project name or base URL.</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          className="flex flex-col gap-4"
        >
          <FormField label="Name" htmlFor="edit-project-name" error={form.formState.errors.name?.message}>
            <Input id="edit-project-name" {...form.register("name")} />
          </FormField>
          <FormField label="Base URL" htmlFor="edit-project-url" error={form.formState.errors.url?.message}>
            <Input id="edit-project-url" {...form.register("url")} />
          </FormField>
          <FormDialogFooter
            onCancel={onClose}
            isSubmitting={mutation.isPending}
            submitLabel="Save"
            submittingLabel="Saving…"
          />
        </form>
      </DialogContent>
    </Dialog>
  )
}
