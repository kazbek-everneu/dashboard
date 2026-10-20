import { useEffect } from "react"
import { useLocation } from "react-router-dom"

import { useBreadcrumbStore, type Crumb } from "@/store/breadcrumbs"

/**
 * Names the current page in the header breadcrumb, e.g. with a project or
 * task name. Pages that don't call it get a trail derived from the URL.
 */
export function useBreadcrumbs(crumbs: Crumb[]) {
  const { pathname } = useLocation()
  const setCrumbs = useBreadcrumbStore((s) => s.setCrumbs)
  // Callers pass a fresh array each render; compare by value.
  const serialized = JSON.stringify(crumbs)

  useEffect(() => {
    setCrumbs(pathname, JSON.parse(serialized) as Crumb[])
  }, [pathname, serialized, setCrumbs])
}
