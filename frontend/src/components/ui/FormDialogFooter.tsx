import { Button } from "@/components/ui/button"
import { DialogFooter } from "@/components/ui/dialog"
import { Spinner } from "@/components/ui/spinner"

interface FormDialogFooterProps {
  onCancel: () => void
  isSubmitting?: boolean
  cancelLabel?: string
  submitLabel: string
  submittingLabel: string
}

/** Footer for a dialog form; render it as the form's last child. */
export function FormDialogFooter({
  onCancel,
  isSubmitting = false,
  cancelLabel = "Cancel",
  submitLabel,
  submittingLabel,
}: FormDialogFooterProps) {
  return (
    <DialogFooter>
      <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
        {cancelLabel}
      </Button>
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting && <Spinner data-icon="inline-start" />}
        {isSubmitting ? submittingLabel : submitLabel}
      </Button>
    </DialogFooter>
  )
}
