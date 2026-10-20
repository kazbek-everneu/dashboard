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
import { Spinner } from "@/components/ui/spinner"
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
        <>
          Don&apos;t have an account?{" "}
          <Link to="/register" className="font-medium text-foreground underline underline-offset-4">
            Register
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
        <FormField label="Email" htmlFor="email" error={errors.email?.message}>
          <Input id="email" type="email" placeholder="you@example.com" {...register("email")} />
        </FormField>
        <FormField label="Password" htmlFor="password" error={errors.password?.message}>
          <Input id="password" type="password" {...register("password")} />
        </FormField>
        <Link
          to="/forgot-password"
          className="-mt-2 self-end text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          Forgot your password?
        </Link>
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting && <Spinner data-icon="inline-start" />}
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthCardLayout>
  )
}
