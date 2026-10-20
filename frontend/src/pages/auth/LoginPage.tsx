import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Link, useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { authApi } from "@/api/auth"
import { useAuth } from "@/hooks/useAuth"
import { extractApiError } from "@/lib/helpers"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { FormField } from "@/components/ui/FormField"
import { AuthCardLayout } from "@/components/features/auth/AuthCardLayout"

const schema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(8, "Password must be at least 8 characters"),
})

type FormValues = z.infer<typeof schema>

const SESSION_COOKIE_ERROR =
  "Signed in, but the session cookie was not accepted. Check dev cookie/CORS settings and try again."

export default function LoginPage() {
  const navigate = useNavigate()
  const { login, isAuthenticated } = useAuth()

  useEffect(() => {
    if (isAuthenticated) navigate("/projects", { replace: true })
  }, [isAuthenticated, navigate])

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) })

  const onSubmit = async (values: FormValues) => {
    try {
      await authApi.login(values.email, values.password)
    } catch (err) {
      toast.error(extractApiError(err))
      return
    }

    try {
      const user = await authApi.me()
      login(user)
      navigate("/projects", { replace: true })
    } catch (err) {
      const message = extractApiError(err)
      toast.error(message === "Not authenticated" ? SESSION_COOKIE_ERROR : message)
    }
  }

  return (
    <AuthCardLayout
      title="QA Screenshot Tool"
      description="Sign in to your account"
      footer={
        <p className="mt-4 text-center text-sm text-muted-foreground">
          Don&apos;t have an account?{" "}
          <Link to="/register" className="text-primary underline underline-offset-4">
            Register
          </Link>
        </p>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <FormField label="Email" htmlFor="email" error={errors.email?.message}>
          <Input id="email" type="email" placeholder="you@example.com" {...register("email")} />
        </FormField>
        <FormField label="Password" htmlFor="password" error={errors.password?.message}>
          <Input id="password" type="password" {...register("password")} />
        </FormField>
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthCardLayout>
  )
}
