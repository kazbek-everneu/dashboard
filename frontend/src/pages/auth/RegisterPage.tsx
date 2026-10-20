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

const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(255, "Name is too long"),
    email: z.string().email("Enter a valid email address"),
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirmPassword: z.string(),
  })
  .refine((d) => d.password === d.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  })

type FormValues = z.infer<typeof schema>

const SESSION_COOKIE_ERROR =
  "Account created, but the session cookie was not accepted. Check dev cookie/CORS settings and sign in again."

export default function RegisterPage() {
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
      await authApi.register({
        name: values.name,
        email: values.email,
        password: values.password,
      })
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
      title="Create account"
      description="Get started with QA Screenshot Tool"
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-foreground underline underline-offset-4">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
        <FormField label="Name" htmlFor="name" error={errors.name?.message}>
          <Input id="name" placeholder="Jane Doe" {...register("name")} />
        </FormField>
        <FormField label="Email" htmlFor="email" error={errors.email?.message}>
          <Input id="email" type="email" placeholder="you@example.com" {...register("email")} />
        </FormField>
        <FormField label="Password" htmlFor="password" error={errors.password?.message}>
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
          {isSubmitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthCardLayout>
  )
}
