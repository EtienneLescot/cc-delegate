export interface Config {
  workerApiKey: string;
  apiKeyEnvVar: string;
  model: string;
  defaultRecursionLimit: number;
  defaultRubricMaxIterations: number;
  defaultMaxBudgetUsd: number;
  defaultTimeoutMs: number;
  workDir: string;
}

export function loadConfig(): Config {
  const workerApiKey = process.env.DELEGATE_API_KEY;
  if (!workerApiKey) throw new Error("DELEGATE_API_KEY is required");
  return {
    workerApiKey,
    // Provider-specific env var litellm reads for DELEGATE_MODEL's provider prefix
    // (e.g. MINIMAX_API_KEY for "minimax/...", OPENAI_API_KEY for "openai/...").
    // Change this alongside DELEGATE_MODEL when switching providers.
    apiKeyEnvVar: process.env.DELEGATE_API_KEY_ENV_VAR ?? "MINIMAX_API_KEY",
    // litellm-routed model string; current default targets MiniMax M3. Swap the
    // provider prefix (see https://docs.litellm.ai/docs/providers) to switch providers.
    model: process.env.DELEGATE_MODEL ?? "litellm:minimax/MiniMax-M3",
    // LangGraph step count, not "turns" — counts every model call and tool call
    // individually, so this needs to be well above what Config.defaultMaxTurns
    // meant under the old Claude-Agent-SDK worker.
    defaultRecursionLimit: Number(process.env.DELEGATE_RECURSION_LIMIT ?? 400),
    // RubricMiddleware grading attempts against definition_of_done/test_command
    // before giving up — the convergence check that replaces trusting the
    // model's own "I'm done" judgment.
    defaultRubricMaxIterations: Number(process.env.DELEGATE_RUBRIC_MAX_ITERATIONS ?? 6),
    // Not yet enforced mid-run (deepagents/LangGraph has no built-in cost meter);
    // accepted for forward compatibility and reported as unavailable in results.
    defaultMaxBudgetUsd: Number(process.env.DELEGATE_MAX_BUDGET_USD ?? 5),
    defaultTimeoutMs: Number(process.env.DELEGATE_TIMEOUT_MS ?? 1_800_000),
    workDir: process.env.DELEGATE_WORK_DIR ?? ".cc-delegate",
  };
}
