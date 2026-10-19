import { Button } from "@/components/ui/button"

interface FormDialogFooterProps {
  onCancel: () => void
  isSubmitting?: boolean
  cancelLabel?: string
  submitLabel: string
  submittingLabel: string
}

export function FormDialogFooter({
  onCancel,
  isSubmitting = false,
  cancelLabel = "Cancel",
  submitLabel,
  submittingLabel,
}: FormDialogFooterProps) {
  return (
    <div className="flex justify-end gap-2 pt-2">
      <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
        {cancelLabel}
      </Button>
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? submittingLabel : submitLabel}
      </Button>
    </div>
  )
}

