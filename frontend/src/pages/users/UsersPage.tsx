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
import { DateTime } from "@/components/ui/DateTime"
import PageHeader from "@/components/layout/PageHeader"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
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
  DialogFooter,
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
    header: "User",
    cell: (row) => (
      <div className="flex items-center gap-3">
        <Avatar className="size-8 rounded-lg">
          <AvatarFallback className="rounded-lg text-xs font-semibold">
            {(row.name || row.email).slice(0, 2).toUpperCase()}
          </AvatarFallback>
        </Avatar>
        <div className="grid min-w-0 leading-tight">
          <span className="truncate font-medium text-foreground">{row.name}</span>
          <span className="truncate text-xs text-muted-foreground">{row.email}</span>
        </div>
      </div>
    ),
  },
  {
    key: "role",
    header: "Role",
    width: "w-[160px]",
    cell: (row) =>
      row.is_admin ? (
        <Badge>
          <ShieldCheck data-icon="inline-start" />
          Admin
        </Badge>
      ) : (
        <Badge variant="secondary">User</Badge>
      ),
  },
  {
    key: "created_at",
    header: "Created",
    width: "w-[220px]",
    hideOnMobile: true,
    cell: (row) => <DateTime value={row.created_at} className="text-muted-foreground" />,
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
    <div className="flex flex-col gap-6">
      <PageHeader
        title={<h1 className="text-2xl font-semibold tracking-tight">Users</h1>}
        description="View registered users and create new accounts."
        actions={
          <Button onClick={openCreateModal} className="shrink-0">
            <Plus data-icon="inline-start" />
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
              icon: <Pencil />,
              onClick: () => openEditModal(row),
            },
            {
              label: "Copy email",
              icon: <Copy />,
              onClick: () => void handleCopyEmail(row.email),
            },
          ]

          if (currentUser?.id !== row.id) {
            actions.push({
              label: "Delete User",
              icon: <Trash2 />,
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
              <Plus data-icon="inline-start" />
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

          <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
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

            <FieldLabel htmlFor="user-is-admin">
              <Field orientation="horizontal">
                <FieldContent>
                  <FieldTitle>Administrator</FieldTitle>
                  <FieldDescription>Can create users and manage administrator access.</FieldDescription>
                </FieldContent>
                <Switch
                  id="user-is-admin"
                  checked={isAdminValue}
                  onCheckedChange={(checked) =>
                    setValue("is_admin", checked, { shouldDirty: true })
                  }
                />
              </Field>
            </FieldLabel>

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

          <div className="flex flex-col gap-4">
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
                <FieldError>There is no active user available for reassignment.</FieldError>
              ) : null}
            </FormField>

            <DialogFooter>
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
                {deleteUserMutation.isPending && <Spinner data-icon="inline-start" />}
                {deleteUserMutation.isPending ? "Deleting…" : "Delete User"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
