import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Users as UsersIcon, Copy, Pencil, ShieldCheck, Trash2 } from "lucide-react"
import { z } from "zod"
import { toast } from "sonner"

import { usersApi } from "@/api/users"
import { useAuth } from "@/hooks/useAuth"
import type { User } from "@/types"
import { extractApiError } from "@/lib/helpers"
import { formatDate } from "@/lib/formatters"
import PageHeader from "@/components/layout/PageHeader"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { FormField } from "@/components/ui/FormField"
import { FormDialogFooter } from "@/components/ui/FormDialogFooter"
import { DataTable, type ColumnDef, type RowAction } from "@/components/ui/DataTable"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const PAGE_SIZE = 20

const userFormSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(255, "Name is too long"),
  email: z.string().email("Enter a valid email address"),
  password: z.string(),
  is_admin: z.boolean(),
})

type UserFormValues = z.infer<typeof userFormSchema>

const userColumns: ColumnDef<User>[] = [
  {
    key: "name",
    header: "Name",
    width: "w-[240px]",
    cell: (row) => <span className="font-medium text-foreground">{row.name}</span>,
  },
  {
    key: "email",
    header: "Email",
    width: "w-[280px]",
    cell: (row) => <span className="text-muted-foreground">{row.email}</span>,
  },
  {
    key: "role",
    header: "Role",
    width: "w-[160px]",
    cell: (row) =>
      row.is_admin ? (
        <Badge className="gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5" />
          Admin
        </Badge>
      ) : (
        <Badge variant="secondary">User</Badge>
      ),
  },
  {
    key: "created_at",
    header: "Created date",
    width: "w-[220px]",
    cell: (row) => <span className="text-muted-foreground">{formatDate(row.created_at)}</span>,
  },
]

export default function UsersPage() {
  const queryClient = useQueryClient()
  const { user: currentUser, setUser } = useAuth()
  const [page, setPage] = useState(1)
  const [formOpen, setFormOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null)
  const [reassignToUserId, setReassignToUserId] = useState("")

  const usersQuery = useQuery({
    queryKey: ["users", page],
    queryFn: () =>
      usersApi.list({
        limit: PAGE_SIZE,
        skip: (page - 1) * PAGE_SIZE,
      }),
  })

  const reassignmentUsersQuery = useQuery({
    queryKey: ["users", "reassignment-targets"],
    queryFn: () => usersApi.list({ limit: 200, skip: 0 }),
    enabled: deleteTarget !== null,
  })

  const {
    register,
    handleSubmit,
    reset,
    setError,
    setValue,
    watch,
    formState: { errors },
  } = useForm<UserFormValues>({
    resolver: zodResolver(userFormSchema),
    defaultValues: {
      name: "",
      email: "",
      password: "",
      is_admin: false,
    },
  })
  const isAdminValue = watch("is_admin")

  const createUserMutation = useMutation({
    mutationFn: usersApi.create,
    onSuccess: async () => {
      toast.success("User created.")
      setFormOpen(false)
      setEditingUser(null)
      reset()
      setPage(1)
      await queryClient.invalidateQueries({ queryKey: ["users"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const updateUserMutation = useMutation({
    mutationFn: ({
      id,
      name,
      password,
      is_admin,
    }: {
      id: string
      name: string
      password?: string
      is_admin: boolean
    }) => usersApi.update(id, { name, password, is_admin }),
    onSuccess: async (updatedUser) => {
      toast.success("User updated.")
      if (currentUser?.id === updatedUser.id) {
        setUser(updatedUser)
      }
      setFormOpen(false)
      setEditingUser(null)
      reset()
      await queryClient.invalidateQueries({ queryKey: ["users"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const deleteUserMutation = useMutation({
    mutationFn: ({
      id,
      reassignToUserId,
    }: {
      id: string
      reassignToUserId: string
    }) => usersApi.delete(id, reassignToUserId),
    onSuccess: async () => {
      toast.success("User deleted.")
      setDeleteTarget(null)
      setReassignToUserId("")
      await queryClient.invalidateQueries({ queryKey: ["users"] })
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const openCreateModal = () => {
    setEditingUser(null)
    reset({ name: "", email: "", password: "", is_admin: false })
    setFormOpen(true)
  }

  const openEditModal = (user: User) => {
    setEditingUser(user)
    reset({
      name: user.name,
      email: user.email,
      password: "",
      is_admin: user.is_admin,
    })
    setFormOpen(true)
  }

  const openDeleteModal = (user: User) => {
    setDeleteTarget(user)
    setReassignToUserId("")
  }

  const onSubmit = (values: UserFormValues) => {
    const trimmedName = values.name.trim()

    if (!editingUser) {
      if (values.password.length < 8) {
        setError("password", {
          type: "manual",
          message: "Password must be at least 8 characters",
        })
        return
      }

      createUserMutation.mutate({
        name: trimmedName,
        email: values.email,
        password: values.password,
        is_admin: values.is_admin,
      })
      return
    }

    if (values.password && values.password.length < 8) {
      setError("password", {
        type: "manual",
        message: "Password must be at least 8 characters",
      })
      return
    }

    updateUserMutation.mutate({
      id: editingUser.id,
      name: trimmedName,
      password: values.password || undefined,
      is_admin: values.is_admin,
    })
  }

  const handleCopyEmail = async (email: string) => {
    try {
      await navigator.clipboard.writeText(email)
      toast.success("Email copied.")
    } catch {
      toast.error("Failed to copy email.")
    }
  }

  const handleDeleteUser = () => {
    if (!deleteTarget || !reassignToUserId) return
    deleteUserMutation.mutate({
      id: deleteTarget.id,
      reassignToUserId,
    })
  }

  const isSubmitting = createUserMutation.isPending || updateUserMutation.isPending
  const isEditMode = editingUser !== null
  const reassignmentUsers =
    reassignmentUsersQuery.data?.items ?? usersQuery.data?.items ?? []
  const reassignmentCandidates = reassignmentUsers.filter(
    (user) => user.is_active && user.id !== deleteTarget?.id,
  )

  return (
    <div className="space-y-6">
      <PageHeader
        title={<h1 className="text-2xl font-bold text-foreground">Users</h1>}
        description="View registered users and create new accounts."
        actions={
          <Button onClick={openCreateModal} className="shrink-0">
            <Plus className="h-4 w-4 mr-2" />
            Add User
          </Button>
        }
      />

      <DataTable
        columns={userColumns}
        data={usersQuery.data?.items ?? []}
        rowKey={(row) => row.id}
        isLoading={usersQuery.isLoading}
        rowActions={(row) => {
          const actions: RowAction<User>[] = [
            {
              label: "Edit User",
              icon: <Pencil className="h-4 w-4" />,
              onClick: () => openEditModal(row),
            },
            {
              label: "Copy email",
              icon: <Copy className="h-4 w-4" />,
              onClick: () => void handleCopyEmail(row.email),
            },
          ]

          if (currentUser?.id !== row.id) {
            actions.push({
              label: "Delete User",
              icon: <Trash2 className="h-4 w-4" />,
              onClick: () => openDeleteModal(row),
              variant: "destructive" as const,
            })
          }

          return actions
        }}
        emptyState={{
          icon: <UsersIcon />,
          title: "No users yet",
          description: "Create the first account to start using the dashboard.",
          action: (
            <Button onClick={openCreateModal}>
              <Plus className="h-4 w-4 mr-2" />
              Add User
            </Button>
          ),
        }}
        pagination={{
          page,
          pageSize: PAGE_SIZE,
          total: usersQuery.data?.total ?? 0,
          onPageChange: setPage,
        }}
      />

      <Dialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open)
          if (!open) {
            setEditingUser(null)
            reset()
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{isEditMode ? "Edit User" : "Create User"}</DialogTitle>
            <DialogDescription>
              {isEditMode
                ? "Update the user's profile, password, or administrator access."
                : "Add a new dashboard user and choose their access level."}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <FormField label="Name" htmlFor="user-name" error={errors.name?.message}>
              <Input id="user-name" placeholder="Jane Doe" {...register("name")} />
            </FormField>

            <FormField label="Email" htmlFor="user-email" error={errors.email?.message}>
              <Input
                id="user-email"
                type="email"
                placeholder="jane@example.com"
                {...register("email")}
                readOnly={isEditMode}
                aria-readonly={isEditMode}
                className={isEditMode ? "bg-muted text-muted-foreground" : undefined}
              />
            </FormField>

            <FormField
              label={isEditMode ? "New Password" : "Password"}
              htmlFor="user-password"
              error={errors.password?.message}
              hint={
                isEditMode ? "Leave blank to keep the current password." : undefined
              }
            >
              <Input id="user-password" type="password" {...register("password")} />
            </FormField>

            <div className="flex items-center justify-between border border-border bg-muted/30 px-3 py-3">
              <div className="space-y-1">
                <label
                  htmlFor="user-is-admin"
                  className="text-sm font-medium text-foreground"
                >
                  Administrator
                </label>
                <p className="text-xs text-muted-foreground">
                  Can create users and manage administrator access.
                </p>
              </div>
              <Switch
                id="user-is-admin"
                checked={isAdminValue}
                onCheckedChange={(checked) =>
                  setValue("is_admin", checked, { shouldDirty: true })
                }
              />
            </div>

            <FormDialogFooter
              onCancel={() => setFormOpen(false)}
              isSubmitting={isSubmitting}
              submitLabel={isEditMode ? "Save Changes" : "Create User"}
              submittingLabel={isEditMode ? "Saving..." : "Creating..."}
            />
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null)
            setReassignToUserId("")
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete User</DialogTitle>
            <DialogDescription>
              Reassign projects and tasks from{" "}
              {deleteTarget?.email ?? "this user"} before deleting the account.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <FormField
              label="Reassign to"
              htmlFor="user-reassign-target"
              hint="Projects and tasks will remain available to the selected user."
            >
              <Select
                value={reassignToUserId}
                onValueChange={setReassignToUserId}
                disabled={reassignmentUsersQuery.isLoading || deleteUserMutation.isPending}
              >
                <SelectTrigger id="user-reassign-target" className="w-full">
                  <SelectValue
                    placeholder={
                      reassignmentUsersQuery.isLoading
                        ? "Loading users..."
                        : "Select a user"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {reassignmentCandidates.map((user) => (
                    <SelectItem key={user.id} value={user.id}>
                      {user.name} - {user.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!reassignmentUsersQuery.isLoading && reassignmentCandidates.length === 0 ? (
                <p className="text-sm text-destructive">
                  There is no active user available for reassignment.
                </p>
              ) : null}
            </FormField>

            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                onClick={() => {
                  setDeleteTarget(null)
                  setReassignToUserId("")
                }}
                disabled={deleteUserMutation.isPending}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={handleDeleteUser}
                disabled={
                  deleteUserMutation.isPending ||
                  !reassignToUserId ||
                  reassignmentCandidates.length === 0
                }
              >
                {deleteUserMutation.isPending ? "Deleting..." : "Delete User"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
