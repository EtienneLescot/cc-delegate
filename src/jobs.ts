import { execa } from "execa";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { Config } from "./config.js";

export type JobStatus = "running" | "succeeded" | "failed";

export interface Job {
  taskId: string;
  status: JobStatus;
  progress?: string;
  turns: number;
  costUsd: number;
  error?: string;
  summary?: string;
  patchPath?: string;
  filesChanged?: string[];
  tests?: { command?: string; passed?: boolean; outputTail?: string };
  branch: string;
  worktree: string;
  repo: string;
  abort: AbortController;
}

const jobs = new Map<string, Job>();

export const getJob = (id: string) => jobs.get(id);
export const putJob = (job: Job) => void jobs.set(job.taskId, job);

/** Crée une branche + worktree jetable pour isoler les écritures du worker. */
export async function createWorktree(cfg: Config, repoPath: string, baseBranch?: string) {
  const repo = resolve(repoPath);
  const taskId = `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const branch = `mm/${taskId}`;
  const wtRoot = join(repo, cfg.workDir, "worktrees");
  await mkdir(wtRoot, { recursive: true });
  const worktree = join(wtRoot, taskId);
  const base = baseBranch ?? (await currentBranch(repo));
  await execa("git", ["worktree", "add", "-b", branch, worktree, base], { cwd: repo });
  return { taskId, branch, worktree, repo };
}

async function currentBranch(repo: string) {
  const { stdout } = await execa("git", ["rev-parse", "--abbrev-ref", "HEAD"], { cwd: repo });
  return stdout.trim();
}

/** Produit un patch git + la liste des fichiers modifiés dans le worktree. */
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
