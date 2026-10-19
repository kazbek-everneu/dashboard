import { useEffect } from "react"
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { Toaster } from "@/components/ui/sonner"
import { authApi } from "@/api/auth"
import { useAuth } from "@/hooks/useAuth"
import AppLayout from "@/components/layout/AppLayout"
import ForgotPasswordPage from "@/pages/auth/ForgotPasswordPage"
import LoginPage from "@/pages/auth/LoginPage"
import RegisterPage from "@/pages/auth/RegisterPage"
import ResetPasswordPage from "@/pages/auth/ResetPasswordPage"
import ProjectsPage from "@/pages/projects/ProjectsPage"
import ProjectDetailPage from "@/pages/projects/ProjectDetailPage"
import TasksPage from "@/pages/tasks/TasksPage"
import TaskDetailPage from "@/pages/tasks/TaskDetailPage"
import AllTasksPage from "@/pages/tasks/AllTasksPage"
import CreateTaskPage from "@/pages/tasks/CreateTaskPage"
import { SettingsPage } from "@/pages/settings/SettingsPage"
import { ScreenshotDetailPage } from "@/pages/screenshots/ScreenshotDetailPage"
import UsersPage from "@/pages/users/UsersPage"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, initialized } = useAuth()
  if (!initialized) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, initialized } = useAuth()
  if (!initialized) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>
  if (isAuthenticated) return <Navigate to="/projects" replace />
  return <>{children}</>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user?.is_admin) return <Navigate to="/projects" replace />
  return <>{children}</>
}

function AuthBootstrap({ children }: { children: React.ReactNode }) {
  const { login, logout, setInitialized } = useAuth()

  const authQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: authApi.me,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (authQuery.data) {
      login(authQuery.data)
      setInitialized(true)
    } else if (authQuery.isError) {
      logout()
      setInitialized(true)
    }
  }, [authQuery.data, authQuery.isError, login, logout, setInitialized])

  useEffect(() => {
    const handleLogout = () => {
      logout()
      setInitialized(true)
    }
    window.addEventListener("auth:logout", handleLogout)
    return () => window.removeEventListener("auth:logout", handleLogout)
  }, [logout, setInitialized])

  useEffect(() => {
    if (authQuery.isPending) return
    if (!authQuery.data && !authQuery.isError) {
      setInitialized(true)
    }
  }, [authQuery.data, authQuery.isError, authQuery.isPending, setInitialized])

  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthBootstrap>
        <BrowserRouter>
          <Routes>
            <Route
              path="/login"
              element={
                <PublicRoute>
                  <LoginPage />
                </PublicRoute>
              }
            />
            <Route
              path="/register"
              element={
                <PublicRoute>
                  <RegisterPage />
                </PublicRoute>
              }
            />
            <Route
              path="/forgot-password"
              element={
                <PublicRoute>
                  <ForgotPasswordPage />
                </PublicRoute>
              }
            />
            <Route
              path="/reset-password"
              element={
                <PublicRoute>
                  <ResetPasswordPage />
                </PublicRoute>
              }
            />
            <Route
              element={
                <ProtectedRoute>
                  <AppLayout />
                </ProtectedRoute>
              }
            >
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:id" element={<ProjectDetailPage />} />
              <Route path="/projects/:id/tasks" element={<TasksPage />} />
              <Route path="/tasks" element={<AllTasksPage />} />
              <Route path="/tasks/new" element={<CreateTaskPage />} />
              <Route path="/tasks/:id" element={<TaskDetailPage />} />
              <Route
                path="/users"
                element={
                  <AdminRoute>
                    <UsersPage />
                  </AdminRoute>
                }
              />
              <Route path="/settings" element={<SettingsPage />} />
              <Route
                path="/tasks/:taskId/screenshots/:screenshotId"
                element={<ScreenshotDetailPage />}
              />
            </Route>
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthBootstrap>
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  )
}
