export type ScoreDirection = "positive" | "neutral" | "negative";

/** Display band for an Alpha Score out of 100. Not investment advice. */
export function scoreDirection(score: number): ScoreDirection {
  if (score >= 60) return "positive";
  if (score >= 40) return "neutral";
  return "negative";
}
