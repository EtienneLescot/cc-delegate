import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

export type JobStatus = "running" | "succeeded" | "failed";

export interface Job {
  taskId: string;
  status: JobStatus;
  progress?: string;
  turns: number;
  costUsd: number | null;
  totalTokens?: number | null;
  error?: string;
  summary?: string;
  patchPath?: string;
  filesChanged?: string[];
  tests?: { command?: string; passed?: boolean; outputTail?: string };
  branch: string;
  worktree: string;
  repo: string;
  abort?: AbortController;
}

export interface WorkerResult {
  status: "succeeded" | "failed";
  summary: string | null;
  turns: number;
  error: string | null;
  rubric_status: string | null;
  cost_usd: number | null;
  total_tokens: number | null;
}

const RESULT_MARKER = "RESULT_JSON:";
const PROGRESS_MARKER = "PROGRESS:";

export function findLastResultLine(stdout: string): string | null {
  const lines = stdout.split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].startsWith(RESULT_MARKER)) return lines[i];
  }
  return null;
}

export function stripResultMarker(line: string): string {
  return line.startsWith(RESULT_MARKER) ? line.slice(RESULT_MARKER.length) : line;
}

/**
 * Shape of a PROGRESS line payload printed by the Python worker to stdout
 * during a run. Each field is optional so we tolerate partial updates.
 */
export interface ProgressEvent {
  step?: number;
  node?: string;
  note?: string;
}

/**
 * Returns the parsed JSON object from a `PROGRESS:...` line, or null if the
 * line is not a PROGRESS line or its payload is malformed. Always returns
 * null (never throws) so the consumer can ignore garbage output.
 */
export function parseProgressLine(line: string): ProgressEvent | null {
  if (!line.startsWith(PROGRESS_MARKER)) return null;
  try {
    const obj = JSON.parse(line.slice(PROGRESS_MARKER.length));
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    return obj as ProgressEvent;
  } catch {
    return null;
  }
}

/**
 * Picks a human-readable note string out of a parsed PROGRESS payload.
 * Prefers an explicit `note`; falls back to `node#step` then `step N`.
 */
export function progressNote(parsed: ProgressEvent): string {
  if (typeof parsed.note === "string" && parsed.note.length > 0) return parsed.note;
  if (typeof parsed.node === "string" && parsed.node.length > 0) return `${parsed.node}#${parsed.step ?? "?"}`;
  return `step ${parsed.step ?? "?"}`;
}

function jobsDir(repo: string, workDir: string): string {
  return join(repo, workDir, "jobs");
}

export function jobFilePath(repo: string, taskId: string, workDir = ".cc-delegate"): string {
  return join(jobsDir(repo, workDir), `${taskId}.json`);
}

export function serializeJob(job: Job): string {
  const { abort: _abort, ...rest } = job;
  void _abort;
  return JSON.stringify(rest, null, 2);
}

export function deserializeJob(raw: string): Job {
  return JSON.parse(raw) as Job;
}

export async function saveJob(job: Job, workDir = ".cc-delegate"): Promise<string> {
  const path = jobFilePath(job.repo, job.taskId, workDir);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, serializeJob(job), "utf8");
  return path;
}

export async function loadJob(repo: string, taskId: string, workDir = ".cc-delegate"): Promise<Job | null> {
  const path = jobFilePath(repo, taskId, workDir);
  try {
    const raw = await readFile(path, "utf8");
    return deserializeJob(raw);
  } catch (e: any) {
    if (e?.code === "ENOENT") return null;
    throw e;
  }
}

export async function deletePersistedJob(job: Job, workDir = ".cc-delegate"): Promise<void> {
  const path = jobFilePath(job.repo, job.taskId, workDir);
  try {
    await unlink(path);
  } catch (e: any) {
    if (e?.code !== "ENOENT") throw e;
  }
}

export const knownRepos = new Set<string>();

export function rememberRepo(repo: string): void {
  if (repo) knownRepos.add(repo);
}

export async function findPersistedJob(taskId: string, workDir = ".cc-delegate"): Promise<Job | null> {
  for (const repo of knownRepos) {
    const j = await loadJob(repo, taskId, workDir);
    if (j) return j;
  }
  return null;
}
