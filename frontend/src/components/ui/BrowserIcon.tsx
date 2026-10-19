import ChromeIcon from "@/assets/icons/chrome.svg?react"
import ChromiumIcon from "@/assets/icons/chromium.svg?react"
import FirefoxIcon from "@/assets/icons/firefox.svg?react"
import WebkitIcon from "@/assets/icons/webkit.svg?react"
import EdgeIcon from "@/assets/icons/edge.svg?react"

interface BrowserIconProps {
  browser: string
  size?: number
  className?: string
}

export function BrowserIcon({ browser, size = 16, className }: BrowserIconProps) {
  const style = { width: size, height: size }
  const props = { style, className, "aria-hidden": true as const }

  switch (browser.toLowerCase()) {
    case "chrome":
      return <ChromeIcon {...props} />
    case "chromium":
      return <ChromiumIcon {...props} />
    case "firefox":
      return <FirefoxIcon {...props} />
    case "webkit":
      return <WebkitIcon {...props} />
    case "msedge":
      return <EdgeIcon {...props} />
    default:
      return (
        <span
          className="inline-flex items-center justify-center border bg-muted font-bold uppercase"
          style={{ width: size, height: size, fontSize: Math.round(size * 0.5) }}
        >
          {browser.slice(0, 2)}
        </span>
      )
  }
}
