import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { TriangleAlert } from "lucide-react"
import { authApi } from "@/api/auth"
import { extractApiError } from "@/lib/helpers"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Spinner } from "@/components/ui/spinner"
import { FormField } from "@/components/ui/FormField"
import { AuthCardLayout } from "@/components/features/auth/AuthCardLayout"

const schema = z
  .object({
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirmPassword: z.string().min(8, "Password must be at least 8 characters"),
  })
  .refine((values) => values.password === values.confirmPassword, {
    path: ["confirmPassword"],
    message: "Passwords do not match",
  })

type FormValues = z.infer<typeof schema>

export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get("token") ?? ""
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = async (values: FormValues) => {
    try {
      await authApi.confirmPasswordReset({ token, password: values.password })
      toast.success("Password has been reset.")
      navigate("/login", { replace: true })
    } catch (err) {
      toast.error(extractApiError(err))
    }
  }

  return (
    <AuthCardLayout
      title="Choose a new password"
      description="Use a password with at least 8 characters"
      footer={
        <>
          Back to{" "}
          <Link to="/login" className="font-medium text-foreground underline underline-offset-4">
            sign in
          </Link>
        </>
      }
    >
      {token ? (
        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <FormField label="New password" htmlFor="password" error={errors.password?.message}>
            <Input id="password" type="password" {...register("password")} />
          </FormField>
          <FormField
            label="Confirm password"
            htmlFor="confirmPassword"
            error={errors.confirmPassword?.message}
          >
            <Input id="confirmPassword" type="password" {...register("confirmPassword")} />
          </FormField>
          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting && <Spinner data-icon="inline-start" />}
          {isSubmitting ? "Saving..." : "Reset password"}
          </Button>
        </form>
      ) : (
        <div className="flex flex-col gap-4">
          <Alert variant="destructive">
            <TriangleAlert />
            <AlertDescription>This reset link is missing a token.</AlertDescription>
          </Alert>
          <Button asChild className="w-full">
            <Link to="/forgot-password">Request a new link</Link>
          </Button>
        </div>
      )}
    </AuthCardLayout>
  )
}
