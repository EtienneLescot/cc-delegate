import { execa } from "execa";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import type { Config } from "./config.js";
import { getJob, collectDiff } from "./jobs.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WORKER_SCRIPT = join(__dirname, "..", "worker", "worker.py");
const RESULT_MARKER = "RESULT_JSON:";

interface RunArgs {
  spec: string;
  worktree: string;
  taskId: string;
  testCommand?: string;
  definitionOfDone?: string;
  recursionLimit: number;
  rubricMaxIterations: number;
}

interface WorkerResult {
  status: "succeeded" | "failed";
  summary: string | null;
  turns: number;
  error: string | null;
  rubric_status: string | null;
}

/** Lance le worker délégué (deepagents, en sous-processus Python) et met à jour le job. */
export async function runWorker(cfg: Config, args: RunArgs): Promise<void> {
  const job = getJob(args.taskId)!;
  try {
    const cliArgs = [
      "run", WORKER_SCRIPT,
      "--worktree", args.worktree,
      "--spec", args.spec,
      "--model", cfg.model,
      "--api-key-env-var", cfg.apiKeyEnvVar,
      "--recursion-limit", String(args.recursionLimit),
      "--rubric-max-iterations", String(args.rubricMaxIterations),
    ];
    if (args.definitionOfDone) cliArgs.push("--definition-of-done", args.definitionOfDone);
    if (args.testCommand) cliArgs.push("--test-command", args.testCommand);

    const { stdout } = await execa("uv", cliArgs, {
      env: { ...process.env, DELEGATE_API_KEY: cfg.workerApiKey },
      cancelSignal: job.abort.signal,
      reject: false,
    });

    const line = stdout.split("\n").reverse().find((l) => l.startsWith(RESULT_MARKER));
    if (!line) {
      job.status = "failed";
      job.error = "worker produced no result line; last stdout: " + stdout.slice(-500);
    } else {
      const result: WorkerResult = JSON.parse(line.slice(RESULT_MARKER.length));
      job.turns = result.turns;
      job.summary = result.summary ?? undefined;
      if (result.status === "succeeded") {
        const { patchPath, filesChanged } = await collectDiff(cfg, job.repo, job.worktree, job.taskId);
        job.patchPath = patchPath;
        job.filesChanged = filesChanged;
        job.status = "succeeded";
      } else {
        job.status = "failed";
        job.error = result.error ?? "worker reported failure";
      }
    }
  } catch (err: any) {
    job.status = "failed";
    if (err?.code === "ENOENT") {
      job.error =
        "'uv' was not found on PATH. The worker needs uv to run worker/worker.py " +
        "(https://docs.astral.sh/uv/getting-started/installation/).";
    } else {
      job.error = err?.message ?? String(err);
    }
  }
}
