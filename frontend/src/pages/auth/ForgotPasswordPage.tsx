import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { MailCheck } from "lucide-react"
import { authApi } from "@/api/auth"
import { extractApiError } from "@/lib/helpers"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Spinner } from "@/components/ui/spinner"
import { FormField } from "@/components/ui/FormField"
import { AuthCardLayout } from "@/components/features/auth/AuthCardLayout"

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
})

type FormValues = z.infer<typeof schema>

export default function ForgotPasswordPage() {
  const [message, setMessage] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = async (values: FormValues) => {
    try {
      const response = await authApi.requestPasswordReset({ email: values.email })
      setMessage(response.message)
    } catch (err) {
      toast.error(extractApiError(err))
    }
  }

  return (
    <AuthCardLayout
      title="Reset password"
      description="Enter your email to receive reset instructions"
      footer={
        <>
          Remembered your password?{" "}
          <Link to="/login" className="font-medium text-foreground underline underline-offset-4">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
        <FormField label="Email" htmlFor="email" error={errors.email?.message}>
          <Input id="email" type="email" placeholder="you@example.com" {...register("email")} />
        </FormField>
        {message ? (
          <Alert>
            <MailCheck />
            <AlertDescription>{message}</AlertDescription>
          </Alert>
        ) : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting && <Spinner data-icon="inline-start" />}
          {isSubmitting ? "Sending..." : "Send reset link"}
        </Button>
      </form>
    </AuthCardLayout>
  )
}
