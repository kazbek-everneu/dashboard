export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

export function shortId(id: string): string {
  return id.slice(0, 8)
}

export function getDeviceType(width: number): "desktop" | "tablet" | "mobile" {
  if (width >= 1024) return "desktop"
  if (width >= 768) return "tablet"
  return "mobile"
}

export function extractApiError(error: unknown): string {
  if (error && typeof error === "object" && "response" in error) {
    const resp = (error as { response?: { data?: { detail?: unknown } } }).response
    const detail = resp?.data?.detail
    if (typeof detail === "string") return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          if (d && typeof d === "object") {
            if ("message" in d && typeof d.message === "string") return d.message
            if ("msg" in d && typeof d.msg === "string") return d.msg
          }
          return "Validation error"
        })
        .join(", ")
    }
  }
  return "An unexpected error occurred."
}
