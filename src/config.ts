export interface Config {
  baseUrl: string;
  workerApiKey: string;
  model: string;
  apiTimeoutMs: number;
  defaultMaxTurns: number;
  defaultMaxBudgetUsd: number;
  defaultTimeoutMs: number;
  workDir: string;
}

export function loadConfig(): Config {
  const workerApiKey = process.env.DELEGATE_API_KEY;
  if (!workerApiKey) throw new Error("DELEGATE_API_KEY is required");
  return {
    // Provider-agnostic: point this at any Anthropic-compatible endpoint (MiniMax, DeepSeek, Kimi, ...).
    baseUrl: process.env.DELEGATE_BASE_URL ?? "https://api.minimax.io/anthropic",
    workerApiKey,
    // Current default targets MiniMax M3; verify the exact model string via the provider's /v1/models.
    model: process.env.DELEGATE_MODEL ?? "MiniMax-M3[1m]",
    apiTimeoutMs: Number(process.env.DELEGATE_API_TIMEOUT_MS ?? 3_000_000),
    defaultMaxTurns: Number(process.env.DELEGATE_MAX_TURNS ?? 120),
    defaultMaxBudgetUsd: Number(process.env.DELEGATE_MAX_BUDGET_USD ?? 5),
    defaultTimeoutMs: Number(process.env.DELEGATE_TIMEOUT_MS ?? 1_800_000),
    workDir: process.env.DELEGATE_WORK_DIR ?? ".cc-delegate",
  };
}
