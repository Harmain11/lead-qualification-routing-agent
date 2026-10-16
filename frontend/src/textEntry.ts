/** Normalize a comma-separated list only after the user finishes editing it. */
export function parseCommaSeparatedList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
