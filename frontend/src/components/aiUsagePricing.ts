export type UsagePricingProvider = "openai" | "gemini" | "claude" | "deepseek" | "xai";

type TokenPrice = {
  inputPerMillionUsd: number;
  outputPerMillionUsd: number;
};

// Standard published text-token rates, USD per million tokens (checked
// 2026-10-15). DeepSeek rates use peak, uncached input pricing; cache discounts
// and time-based rates are not represented by the recorded usage counters.
// Sources: openai.com/api/pricing, ai.google.dev/gemini-api/docs/pricing,
// anthropic.com/pricing, api-docs.deepseek.com/quick_start/pricing,
// docs.x.ai/developers/models/grok-4.7.
const tokenPrices: Record<UsagePricingProvider, Record<string, TokenPrice>> = {
  openai: {
    "gpt-4.1": { inputPerMillionUsd: 2, outputPerMillionUsd: 8 },
    "gpt-4.1-mini": { inputPerMillionUsd: 0.4, outputPerMillionUsd: 1.6 },
  },
  gemini: {
    "gemini-3.8-flash": { inputPerMillionUsd: 0.3, outputPerMillionUsd: 2.5 },
    "gemini-2.5-flash": { inputPerMillionUsd: 0.3, outputPerMillionUsd: 2.5 },
  },
  claude: {
    "claude-sonnet-4-6": { inputPerMillionUsd: 3, outputPerMillionUsd: 15 },
    "claude-haiku-4-5": { inputPerMillionUsd: 1, outputPerMillionUsd: 5 },
  },
  deepseek: {
    "deepseek-flash": { inputPerMillionUsd: 0.3, outputPerMillionUsd: 1.2 },
    "deepseek-v4-pro": { inputPerMillionUsd: 1.32, outputPerMillionUsd: 3.96 },
  },
  xai: {
    "grok-4.7": { inputPerMillionUsd: 2, outputPerMillionUsd: 6 },
  },
};

export function estimateModelCostUsd(
  provider: UsagePricingProvider,
  model: string,
  inputTokens: number,
  outputTokens: number,
): number | null {
  const price = tokenPrices[provider]?.[model];
  if (!price) return null;
  return (
    (Math.max(0, inputTokens) * price.inputPerMillionUsd
      + Math.max(0, outputTokens) * price.outputPerMillionUsd) / 1_000_000
  );
}
