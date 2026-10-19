import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { authApi } from "@/api/auth"
import { extractApiError } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
        <p className="mt-4 text-center text-sm text-muted-foreground">
          Remembered your password?{" "}
          <Link to="/login" className="text-primary underline underline-offset-4">
            Sign in
          </Link>
        </p>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <FormField label="Email" htmlFor="email" error={errors.email?.message}>
          <Input id="email" type="email" placeholder="you@example.com" {...register("email")} />
        </FormField>
        {message ? (
          <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
            {message}
          </p>
        ) : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Sending..." : "Send reset link"}
        </Button>
      </form>
    </AuthCardLayout>
  )
}
