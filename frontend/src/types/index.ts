// ─── Auth ────────────────────────────────────────────────────────────────────

export interface User {
  id: string
  name: string
  email: string
  is_active: boolean
  is_admin: boolean
  created_at: string
  /** IANA zone times are shown in; null follows the browser. */
  timezone: string | null
}

export interface LoginRequest {
  username: string // FastAPI OAuth2 form uses "username"
  password: string
}

export interface CreateUserRequest {
  name: string
  email: string
  password: string
  is_admin?: boolean
}

export interface UpdateUserRequest {
  name?: string
  password?: string
  is_admin?: boolean
  /** null resets to following the browser's time zone. */
  timezone?: string | null
}

export interface TokenResponse {
  access_token: string | null
  token_type: string
}

export interface PasswordResetRequest {
  email: string
}

export interface PasswordResetConfirmRequest {
  token: string
  password: string
}

export interface PasswordResetResponse {
  message: string
}

// ─── Projects ────────────────────────────────────────────────────────────────

export interface Project {
  id: string
  owner_id: string
  name: string
  url: string
  created_at: string
}

export interface ProjectSummary {
  project_id: string
  last_task_status: string | null
  screenshots_count: number
}

/** Simple width×height pair — used for task-level overrides. */
export interface Viewport {
  width: number
  height: number
}

/** Extended viewport entry stored in ProjectConfig.viewports. */
export interface ViewportConfig extends Viewport {
  device_type: "desktop" | "tablet" | "mobile"
  /** null = inherit project default_browsers; string[] = per-viewport override */
  browsers: string[] | null
}

export interface ProjectConfig {
  id: string
  project_id: string
  default_browsers: string[]
  viewports: ViewportConfig[]
  wait_until: "load" | "domcontentloaded" | "networkidle"
  timeout: number
}

export interface ProjectConfigUpdate {
  default_browsers?: string[]
  viewports?: ViewportConfig[]
  wait_until?: "load" | "domcontentloaded" | "networkidle"
  timeout?: number
}

export interface ApplicationSettings {
  id: number
  // Capture
  screenshot_format: "jpeg" | "png"
  screenshot_quality: number
  // Workers & performance
  sem_limit: number
  task_job_timeout_seconds: number
  reuse_browser_context: boolean
  // Security & sessions
  access_token_expire_minutes: number
  password_reset_token_expire_minutes: number
  password_reset_rate_limit_max: number
  password_reset_rate_limit_window_seconds: number
  // Email / SMTP (credentials stay server-side in .env)
  smtp_host: string | null
  smtp_port: number
  smtp_from_email: string
  smtp_use_tls: boolean
  frontend_base_url: string
}

export interface ApplicationSettingsUpdate {
  screenshot_format?: "jpeg" | "png"
  screenshot_quality?: number
  sem_limit?: number
  task_job_timeout_seconds?: number
  reuse_browser_context?: boolean
  access_token_expire_minutes?: number
  password_reset_token_expire_minutes?: number
  password_reset_rate_limit_max?: number
  password_reset_rate_limit_window_seconds?: number
  smtp_host?: string | null
  smtp_port?: number
  smtp_from_email?: string
  smtp_use_tls?: boolean
  frontend_base_url?: string
}

// ─── Sitemap ─────────────────────────────────────────────────────────────────

export interface SitemapURL {
  id: string
  project_id: string
  url: string
  cpt: string
  is_enabled: boolean
  last_fetched_at: string | null
}

// ─── Tasks ───────────────────────────────────────────────────────────────────

export type TaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out"

export interface Task {
  id: string
  project_id: string
  created_by: string
  status: TaskStatus
  progress_total: number
  progress_done: number
  progress_failed: number
  override_browser: string | null
  override_browsers: string[] | null
  override_viewports: ViewportConfig[] | null
  target_urls: string[]
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface CreateTaskRequest {
  project_id: string
  target_urls?: string[]
  override_browser?: string
  override_browsers?: string[]
  override_viewports?: ViewportConfig[]
}

// ─── Screenshots ─────────────────────────────────────────────────────────────

export type ScreenshotStatus = "ok" | "failed" | "recapturing"

/** Codes in the backend's warning registry (app/screenshots/capture/report.py). */
export type KnownCaptureWarningCode =
  | "http_error"
  | "redirected"
  | "challenge_passed"
  | "fonts_timeout"
  | "assets_pending"
  | "assets_missing"
  | "styles_missing"
  | "assets_recovered"
  | "page_reloaded"
  | "recovery_skipped"
  | "dom_unstable"
  | "overflow_cropped"
  | "screenshot_retried"
  | "inner_scroll_expanded"
  | "infinite_scroll_stopped"
  | "overlays_hidden"
  | "carousels_paused"

/** A registry code; any other string still type-checks, so a newer backend's
 *  codes don't break older frontends. */
export type CaptureWarningCode = KnownCaptureWarningCode | (string & {})

export interface CaptureWarning {
  code: CaptureWarningCode
  level: "info" | "warning"
  message: string
  details?: Record<string, unknown>
}

/** capture_meta v1. Every section and field is optional: a failed capture
 *  stores a partial report, and rows older than v1 have no metadata (null). */
export interface CaptureMeta {
  v?: number
  attempts?: number
  browser_version?: string
  device?: {
    type?: "desktop" | "tablet" | "mobile"
    /** "partial" when the engine can't emulate everything (Firefox: no is_mobile). */
    emulation?: string
    is_mobile?: boolean
    has_touch?: boolean
    user_agent?: string
  }
  navigation?: {
    requested_url?: string
    final_url?: string
    http_status?: number | null
    wait_strategy?: string
    load_event?: boolean
    reloads?: number
    challenge?: string | null
  }
  page?: {
    viewport_width?: number
    viewport_height?: number
    document_width?: number
    document_height?: number
    /** Set when the frame was cropped to the layout viewport's width. */
    clipped_width?: number | null
    /** The inner scroll container that was expanded, if any. */
    inner_scroller?: string | boolean | null
    scroll_steps?: number
    pass2?: boolean
  }
  readiness?: {
    stable?: boolean
    fast_path?: boolean
    fonts_ready?: boolean
    second_chance?: boolean
  }
  overlays?: {
    hidden?: number
    /** Up to 10 hidden elements: level, rule, `tag#id.class`, rect. */
    samples?: Record<string, unknown>[]
  }
  /** Phase durations: context, goto, load_wait, quiet, prepare, scroll,
   *  readiness, recovery, reload, snapshot, total. */
  timings_ms?: Record<string, number>
  failure?: {
    class?: string
    phase?: string | null
  }
}

export interface Screenshot {
  id: string
  task_id: string
  url: string
  browser: string
  width: number
  height: number
  image_width: number | null
  image_height: number | null
  file_format: "jpeg" | "png"
  file_path: string
  signed_url: string
  status: ScreenshotStatus
  captured_with_unstable_dom: boolean
  error_message: string | null
  duration_seconds: number
  /** null on rows captured before device emulation — derive it from the width. */
  device_type: "desktop" | "tablet" | "mobile" | null
  /** Empty for clean captures and older rows. */
  warnings: CaptureWarning[]
  capture_meta: CaptureMeta | null
  created_at: string
}

// ─── API response wrappers ───────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[]
  total: number
}

export interface TaskStats {
  total: number
  pending: number
  running: number
  completed: number
  failed: number
  cancelled: number
  timed_out: number
}

export interface ApiError {
  detail: string | { msg: string; type: string }[]
}
