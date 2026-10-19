import { useQuery } from "@tanstack/react-query"
import { tasksApi } from "@/api/tasks"
import type { Task } from "@/types"

const FINAL_STATUSES = new Set(["completed", "failed", "cancelled", "timed_out"])

export function useTaskPolling(taskId: string) {
  return useQuery<Task>({
    queryKey: ["task", taskId],
    queryFn: () => tasksApi.get(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && FINAL_STATUSES.has(status)) return false
      return 3000
    },
  })
}
