export type Density = "default" | "compact"

/** Cell padding shared by header, body and skeleton rows so heights match. */
export const CELL_PADDING: Record<Density, string> = {
  default: "px-4 py-3",
  compact: "px-4 py-2",
}
