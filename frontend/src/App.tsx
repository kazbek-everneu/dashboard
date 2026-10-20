import { useEffect } from "react"
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom"
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { Toaster } from "@/components/ui/sonner"
import { Spinner } from "@/components/ui/spinner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { authApi } from "@/api/auth"
import { useAuth } from "@/hooks/useAuth"
import AppLayout from "@/components/layout/AppLayout"
import ForgotPasswordPage from "@/pages/auth/ForgotPasswordPage"
import LoginPage from "@/pages/auth/LoginPage"
import ResetPasswordPage from "@/pages/auth/ResetPasswordPage"
import ProjectsPage from "@/pages/projects/ProjectsPage"
import ProjectDetailPage from "@/pages/projects/ProjectDetailPage"
import TaskDetailPage from "@/pages/tasks/TaskDetailPage"
import AllTasksPage from "@/pages/tasks/AllTasksPage"
import CreateTaskPage from "@/pages/tasks/CreateTaskPage"
import { SettingsPage } from "@/pages/settings/SettingsPage"
import ProfilePage from "@/pages/profile/ProfilePage"
import { ScreenshotDetailPage } from "@/pages/screenshots/ScreenshotDetailPage"
import UsersPage from "@/pages/users/UsersPage"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

// The old per-project task list is served by AllTasksPage's project filter.
function LegacyProjectTasksRedirect() {
  const { id } = useParams<{ id: string }>()
  return <Navigate to={`/tasks?project_id=${id}`} replace />
}

function FullPageSpinner() {
  return (
    <div className="flex min-h-svh items-center justify-center">
      <Spinner className="size-6 text-muted-foreground" />
    </div>
  )
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, initialized } = useAuth()
  if (!initialized) return <FullPageSpinner />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, initialized } = useAuth()
  if (!initialized) return <FullPageSpinner />
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
      <TooltipProvider>
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
                <Route path="/projects/:id/tasks" element={<LegacyProjectTasksRedirect />} />
                <Route path="/tasks" element={<AllTasksPage />} />
                <Route path="/tasks/new" element={<CreateTaskPage />} />
                <Route path="/tasks/:id" element={<TaskDetailPage />} />
                <Route path="/profile" element={<ProfilePage />} />
                <Route
                  path="/users"
                  element={
                    <AdminRoute>
                      <UsersPage />
                    </AdminRoute>
                  }
                />
                <Route
                  path="/settings"
                  element={
                    <AdminRoute>
                      <SettingsPage />
                    </AdminRoute>
                  }
                />
                <Route
                  path="/tasks/:taskId/screenshots/:screenshotId"
                  element={<ScreenshotDetailPage />}
                />
              </Route>
              <Route path="*" element={<Navigate to="/projects" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthBootstrap>
      </TooltipProvider>
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  )
}
