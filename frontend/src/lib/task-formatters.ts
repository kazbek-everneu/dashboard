export function formatElapsedTime(elapsedMs: number | null): string {
  if (!elapsedMs || elapsedMs <= 0) return "—"
  const totalSeconds = Math.max(1, Math.floor(elapsedMs / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

export function formatThroughput(count: number, elapsedMs: number | null): string {
  if (!elapsedMs || elapsedMs <= 0 || count <= 0) return "—"
  const perSecond = count / (elapsedMs / 1000)
  const formatted =
    perSecond >= 10 ? perSecond.toFixed(0) : perSecond >= 1 ? perSecond.toFixed(1) : perSecond.toFixed(2)
  return `${formatted} shots/s`
}

export function formatAverageSeconds(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—"
  return seconds >= 10 ? `${seconds.toFixed(1)}s` : `${seconds.toFixed(2)}s`
}

export function browserLabel(browser: string): string {
  if (browser === "msedge") return "Edge"
  if (browser === "webkit") return "Safari"
  return browser.charAt(0).toUpperCase() + browser.slice(1)
}
