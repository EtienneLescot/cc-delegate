import { execa } from "execa";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { Config } from "./config.js";
import {
  deletePersistedJob,
  findPersistedJob,
  rememberRepo,
  saveJob,
  type Job,
  type JobStatus,
} from "./persistence.js";

export type { Job, JobStatus };

const jobs = new Map<string, Job>();

export function getJob(id: string): Job | undefined {
  return jobs.get(id);
}

export function putJob(job: Job): void {
  jobs.set(job.taskId, job);
}

export function deleteJob(id: string): void {
  jobs.delete(id);
}

/**
 * Returns the job either from the in-memory registry, or by falling back to
 * the persisted JSON file. Useful for MCP tool handlers that may serve a job
 * created in a previous process lifetime (i.e. after the MCP server restarted).
 */
export async function getJobWithFallback(id: string, cfg: Config): Promise<Job | null> {
  const inMem = jobs.get(id);
  if (inMem) return inMem;
  return await findPersistedJob(id, cfg.workDir);
}

/** Persist the job to disk. Best-effort: a failed write does not abort the run. */
export async function persistJob(job: Job, cfg: Config): Promise<void> {
  try {
    await saveJob(job, cfg.workDir);
  } catch (e) {
    // Persistence is best-effort; the in-memory state remains the source of
    // truth for this process lifetime.
  }
}

/** Create a disposable branch + worktree to isolate the worker writes. */
export async function createWorktree(cfg: Config, repoPath: string, baseBranch?: string) {
  const repo = resolve(repoPath);
  const taskId = `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const branch = `delegate/${taskId}`;
  const wtRoot = join(repo, cfg.workDir, "worktrees");
  await mkdir(wtRoot, { recursive: true });
  const worktree = join(wtRoot, taskId);
  const base = baseBranch ?? (await currentBranch(repo));
  await execa("git", ["worktree", "add", "-b", branch, worktree, base], { cwd: repo });
  rememberRepo(repo);
  return { taskId, branch, worktree, repo };
}

async function currentBranch(repo: string) {
  const { stdout } = await execa("git", ["rev-parse", "--abbrev-ref", "HEAD"], { cwd: repo });
  return stdout.trim();
}

/** Produce a git patch + the list of files modified in the worktree. */
export async function collectDiff(cfg: Config, repo: string, worktree: string, taskId: string) {
  await execa("git", ["add", "-A"], { cwd: worktree });
  const { stdout: diff } = await execa("git", ["diff", "--cached"], { cwd: worktree });
  const { stdout: names } = await execa("git", ["diff", "--cached", "--name-only"], { cwd: worktree });
  const logDir = join(repo, cfg.workDir, "patches");
  await mkdir(logDir, { recursive: true });
  const patchPath = join(logDir, `${taskId}.diff`);
  await writeFile(patchPath, diff, "utf8");
  const filesChanged = names.split("\n").map((s) => s.trim()).filter(Boolean);
  return { patchPath, filesChanged };
}

/**
 * Tear down the worktree, branch, and persisted file for a job. Refuses to
 * touch a job that is still running; the caller should pass a job it knows
 * is in a terminal state.
 */
export async function cleanupJob(
  cfg: Config,
  job: Job,
  opts: { deleteBranch?: boolean } = {},
): Promise<{ taskId: string; worktreeRemoved: boolean; branchDeleted: boolean; persistedRemoved: boolean }> {
  const deleteBranch = opts.deleteBranch ?? true;
  const result = { taskId: job.taskId, worktreeRemoved: false, branchDeleted: false, persistedRemoved: false };

  try {
    await execa("git", ["worktree", "remove", "--force", job.worktree], { cwd: job.repo });
    result.worktreeRemoved = true;
  } catch (e: any) {
    // The worktree may already be gone; ignore ENOENT-style failures.
    if (e?.code !== "ENOENT" && !String(e?.stderr || "").includes("not a working tree")) {
      // Non-fatal: we still try to clean up the branch + persisted file.
    }
  }

  if (deleteBranch) {
    try {
      await execa("git", ["branch", "-D", job.branch], { cwd: job.repo });
      result.branchDeleted = true;
    } catch {
      // Branch may already be gone; ignore.
    }
  }

  try {
    await deletePersistedJob(job, cfg.workDir);
    result.persistedRemoved = true;
  } catch {
    // Best-effort.
  }

  deleteJob(job.taskId);
  return result;
}
