import { execa } from "execa";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import type { Config } from "./config.js";
import { collectDiff, persistJob, getJob, type Job } from "./jobs.js";
import {
  findLastResultLine,
  parseProgressLine,
  progressNote,
  stripResultMarker,
  type WorkerResult,
} from "./persistence.js";

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

async function updateProgress(job: Job, cfg: Config, note: string): Promise<void> {
  job.progress = note.slice(0, 200);
  await persistJob(job, cfg);
}

async function runStream(cfg: Config, args: RunArgs, job: Job): Promise<string> {
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

  let stdoutRemainder = "";
  let resultLine: string | undefined;

  const subprocess = execa("uv", cliArgs, {
    env: { ...process.env, DELEGATE_API_KEY: cfg.workerApiKey },
    cancelSignal: job.abort?.signal,
    reject: false,
    all: true,
  });

  subprocess.stdout?.setEncoding("utf8");
  subprocess.stdout?.on("data", (chunk: string) => {
    stdoutRemainder += chunk;
    let nlIdx: number;
    while ((nlIdx = stdoutRemainder.indexOf("\n")) >= 0) {
      const line = stdoutRemainder.slice(0, nlIdx).replace(/\r$/, "");
      stdoutRemainder = stdoutRemainder.slice(nlIdx + 1);
      // Capture the RESULT_JSON line as it streams past; later writes to
      // job.progress must not clobber it because we keep overwriting the
      // same `resultLine` reference until the loop ends.
      if (line.startsWith(RESULT_MARKER)) {
        resultLine = line;
        continue;
      }
      // Each PROGRESS line advances job.progress to the latest human-readable
      // note (falls back to node#step / "step N"). Anything else — agent
      // verbose output, library chatter — is silently ignored.
      const progress = parseProgressLine(line);
      if (progress) {
        const note = progressNote(progress);
        updateProgress(job, cfg, note).catch(() => {
          // Persistence is best-effort; swallow errors so we don't
          // crash the stdout consumer mid-stream.
        });
      }
    }
  });

  const result = await subprocess;

  // After the process exits there may be one final partial line in the
  // remainder buffer (the writer never wrote a trailing newline).
  if (stdoutRemainder.length > 0) {
    const tail = stdoutRemainder.replace(/\r$/, "");
    if (tail.startsWith(RESULT_MARKER)) resultLine = tail;
    else {
      const progress = parseProgressLine(tail);
      if (progress) {
        updateProgress(job, cfg, progressNote(progress)).catch(() => {});
      }
    }
    stdoutRemainder = "";
  }

  // Use the live-captured line if available; otherwise fall back to
  // result.stdout for subprocesses that buffer everything until exit
  // (older execa releases, very fast producers).
  const stdout = result.stdout ?? "";
  const finalResultLine = resultLine ?? findLastResultLine(stdout);

  if (
    result.signal === "SIGTERM" ||
    result.signal === "SIGABRT" ||
    result.signal === "SIGINT" ||
    job.abort?.signal.aborted
  ) {
    job.status = "failed";
    job.error = "worker aborted";
    await persistJob(job, cfg);
    return finalResultLine ?? "";
  }

  if (!finalResultLine) {
    job.status = "failed";
    job.error = "worker produced no result line; tail: " + (stdout ? stdout.slice(-500) : "").toString();
    await persistJob(job, cfg);
    return "";
  }

  try {
    const result_json: WorkerResult = JSON.parse(stripResultMarker(finalResultLine));
    job.turns = result_json.turns;
    // cost_usd / total_tokens may be absent or null on worker.py revisions
    // that don't (yet) meter them; only copy finite numbers.
    if (typeof result_json.cost_usd === "number" && Number.isFinite(result_json.cost_usd)) {
      job.costUsd = result_json.cost_usd;
    }
    if (typeof result_json.total_tokens === "number" && Number.isFinite(result_json.total_tokens)) {
      job.totalTokens = result_json.total_tokens;
    }
    if (typeof result_json.summary === "string") job.summary = result_json.summary;
    if (result_json.status === "succeeded") {
      const { patchPath, filesChanged } = await collectDiff(cfg, job.repo, job.worktree, job.taskId);
      job.patchPath = patchPath;
      job.filesChanged = filesChanged;
      job.status = "succeeded";
    } else {
      job.status = "failed";
      job.error = result_json.error ?? "worker reported failure";
    }
  } catch (e: any) {
    job.status = "failed";
    job.error = "could not parse result line: " + (e?.message ?? String(e));
  }
  await persistJob(job, cfg);
  return finalResultLine;
}

/** Run the delegated worker (deepagents, in a Python subprocess) and keep the job in sync. */
export async function runWorker(cfg: Config, args: RunArgs): Promise<void> {
  const job = getJob(args.taskId);
  if (!job) {
    // Should never happen: putJob is called by the MCP server before runWorker.
    return;
  }
  try {
    await runStream(cfg, args, job);
  } catch (err: any) {
    job.status = "failed";
    if (err?.code === "ENOENT") {
      job.error =
        "uv was not found on PATH. The worker needs uv to run worker/worker.py " +
        "(https://docs.astral.sh/uv/getting-started/installation/).";
    } else {
      job.error = err?.message ?? String(err);
    }
    await persistJob(job, cfg);
  }
}
