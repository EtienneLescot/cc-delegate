import { query } from "@anthropic-ai/claude-agent-sdk";
import type { Config } from "./config.js";
import { workerAgents } from "./subagents.js";
import { getJob, collectDiff } from "./jobs.js";

interface RunArgs {
  spec: string;
  worktree: string;
  taskId: string;
  testCommand?: string;
  definitionOfDone?: string;
  maxTurns: number;
  maxBudgetUsd: number;
}

function delegateEnv(cfg: Config): Record<string, string> {
  return {
    ...process.env,
    ANTHROPIC_API_KEY: "",
    ANTHROPIC_BASE_URL: cfg.baseUrl,
    ANTHROPIC_AUTH_TOKEN: cfg.workerApiKey,
    ANTHROPIC_MODEL: cfg.model,
    ANTHROPIC_DEFAULT_SONNET_MODEL: cfg.model,
    ANTHROPIC_DEFAULT_OPUS_MODEL: cfg.model,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: cfg.model,
    API_TIMEOUT_MS: String(cfg.apiTimeoutMs),
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",
  } as Record<string, string>;
}

function buildPrompt(a: RunArgs): string {
  return [
    `# Task`, a.spec,
    a.definitionOfDone ? `\n# Definition of done\n${a.definitionOfDone}` : "",
    a.testCommand ? `\n# Test command\nRun \`${a.testCommand}\` and iterate until it passes.` : "",
    `\n# Rules`,
    `- Work only inside the current working directory.`,
    `- Never run git push, merge, rebase onto other branches, or destructive commands.`,
    `- Use the implementer/tester/reviewer subagents when helpful.`,
    `- When done, print a short summary of what changed and the final test status.`,
  ].filter(Boolean).join("\n");
}

/** Lance le worker délégué en tâche de fond et met à jour le job. */
export async function runWorker(cfg: Config, args: RunArgs): Promise<void> {
  const job = getJob(args.taskId)!;
  try {
    const q = query({
      prompt: buildPrompt(args),
      options: {
        cwd: args.worktree,
        env: delegateEnv(cfg),
        model: cfg.model,
        permissionMode: "bypassPermissions",
        allowDangerouslySkipPermissions: true,
        allowedTools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
        agents: workerAgents,
        maxTurns: args.maxTurns,
        maxBudgetUsd: args.maxBudgetUsd,
        settingSources: ["project"],
        strictMcpConfig: true,
        systemPrompt: {
          type: "preset",
          preset: "claude_code",
          append:
            "You are an autonomous coding worker delegated by a supervisor. " +
            "Deliver a complete, tested change. Stop at the patch; the supervisor reviews and merges.",
        },
        abortController: job.abort,
      },
    });

    for await (const msg of q) {
      if (msg.type === "assistant") {
        const block = (msg.message?.content as any[] | undefined)?.find?.((b: any) => b.type === "text");
        const text = block?.text;
        if (text) job.progress = String(text).slice(0, 300);
      } else if (msg.type === "result") {
        job.turns = msg.num_turns ?? job.turns;
        job.costUsd = msg.total_cost_usd ?? job.costUsd;
        if (msg.subtype === "success") {
          job.summary = msg.result;
        } else {
          job.status = "failed";
          job.error = `${msg.subtype}: ${(msg as any).errors?.join?.("; ") ?? "run ended without success"}`;
        }
      }
    }

    if (job.status !== "failed") {
      const { patchPath, filesChanged } = await collectDiff(cfg, job.repo, job.worktree, job.taskId);
      job.patchPath = patchPath;
      job.filesChanged = filesChanged;
      job.status = "succeeded";
    }
  } catch (err: any) {
    job.status = "failed";
    job.error = err?.message ?? String(err);
  }
}
