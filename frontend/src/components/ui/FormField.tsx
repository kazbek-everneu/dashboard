import { cloneElement, isValidElement, useId, type ReactNode } from "react"

import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"

interface FormFieldProps {
  label?: ReactNode
  htmlFor?: string
  error?: ReactNode
  hint?: ReactNode
  required?: boolean
  className?: string
  children: ReactNode
}

export function FormField({
  label,
  htmlFor,
  error,
  hint,
  required = false,
  className,
  children,
}: FormFieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(" ")

  // Point the control at its hint/error so assistive tech announces them.
  const control =
    isValidElement<Record<string, unknown>>(children) && (error || describedBy)
      ? cloneElement(children, {
          ...(error ? { "aria-invalid": true } : {}),
          ...(describedBy ? { "aria-describedby": describedBy } : {}),
        })
      : children

  return (
    <Field data-invalid={error ? true : undefined} className={className}>
      {label ? (
        <FieldLabel htmlFor={htmlFor}>
          {label}
          {required ? <span className="text-destructive">*</span> : null}
        </FieldLabel>
      ) : null}
      {control}
      {hint ? <FieldDescription id={hintId}>{hint}</FieldDescription> : null}
      {error ? <FieldError id={errorId}>{error}</FieldError> : null}
    </Field>
  )
}
