export function recentReviewBatch(limit = 100) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
    throw new Error("Choose a whole number from 1 to 5,000 emails.");
  }
  return { limit, pageSize: Math.min(25, limit) };
}
