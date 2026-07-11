export interface Config {
  baseUrl: string;
  minimaxApiKey: string;
  model: string;
  apiTimeoutMs: number;
  defaultMaxTurns: number;
  defaultMaxBudgetUsd: number;
  defaultTimeoutMs: number;
  workDir: string;
}

export function loadConfig(): Config {
  const minimaxApiKey = process.env.MINIMAX_API_KEY;
  if (!minimaxApiKey) throw new Error("MINIMAX_API_KEY is required");
  return {
    baseUrl: process.env.MINIMAX_BASE_URL ?? "https://api.minimax.io/anthropic",
    minimaxApiKey,
    // Vérifier la chaîne exacte via GET /v1/models ; rendre configurable.
    model: process.env.MINIMAX_MODEL ?? "MiniMax-M3[1m]",
    apiTimeoutMs: Number(process.env.MINIMAX_API_TIMEOUT_MS ?? 3_000_000),
    defaultMaxTurns: Number(process.env.MM_MAX_TURNS ?? 120),
    defaultMaxBudgetUsd: Number(process.env.MM_MAX_BUDGET_USD ?? 5),
    defaultTimeoutMs: Number(process.env.MM_TIMEOUT_MS ?? 1_800_000),
    workDir: process.env.MM_WORK_DIR ?? ".minimax-delegate",
  };
}
