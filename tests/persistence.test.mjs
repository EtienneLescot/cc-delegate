// Round-trip tests for src/persistence.ts: serializeJob must strip AbortController,
// saveJob/loadJob must persist and restore the rest of the Job, and deletePersistedJob
// must remove the file (and tolerate a missing file).
//
// Each filesystem-using test gets its own tmpdir so they can run in parallel under
// `node --test` without colliding.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  deletePersistedJob,
  deserializeJob,
  jobFilePath,
  loadJob,
  rememberRepo,
  saveJob,
  serializeJob,
} from "./build/persistence.js";

function makeJob(extra = {}) {
  return {
    taskId: "t_persist123",
    status: "running",
    turns: 0,
    costUsd: null,
    totalTokens: null,
    branch: "delegate/t_persist123",
    worktree: "/tmp/worktrees/t_persist123",
    repo: "/tmp/repo",
    abort: new AbortController(),
    ...extra,
  };
}

test("serializeJob excludes AbortController from the JSON output", () => {
  const job = makeJob();
  const serialized = serializeJob(job);
  const parsed = JSON.parse(serialized);
  assert.equal(parsed.taskId, "t_persist123");
  // The AbortController must not leak into the persisted JSON.
  assert.equal(parsed.abort, undefined);
  // Sanity: AbortController.toString() / class name must not appear either.
  assert.ok(!serialized.includes("AbortController"));
  assert.ok(!/\"abort\"\s*:/.test(serialized));
});

test("serializeJob/deserializeJob round-trip preserves all non-abort fields", () => {
  const job = makeJob({
    status: "succeeded",
    progress: "all done",
    turns: 7,
    costUsd: 0.42,
    totalTokens: 12345,
    summary: "implemented the feature",
    filesChanged: ["src/foo.ts", "src/bar.ts"],
    patchPath: "/tmp/.cc-delegate/patches/t_persist123.diff",
  });
  const serialized = serializeJob(job);
  const restored = deserializeJob(serialized);
  assert.equal(restored.taskId, job.taskId);
  assert.equal(restored.status, "succeeded");
  assert.equal(restored.progress, "all done");
  assert.equal(restored.turns, 7);
  assert.equal(restored.costUsd, 0.42);
  assert.equal(restored.totalTokens, 12345);
  assert.equal(restored.summary, "implemented the feature");
  assert.deepEqual(restored.filesChanged, ["src/foo.ts", "src/bar.ts"]);
  assert.equal(restored.patchPath, "/tmp/.cc-delegate/patches/t_persist123.diff");
  // Abort stays excluded on the round-trip.
  assert.equal(restored.abort, undefined);
});

test("serializeJob omits the abort key entirely even when abort is undefined", () => {
  const job = makeJob({ abort: undefined });
  const serialized = serializeJob(job);
  assert.ok(!/\"abort\"\s*:/.test(serialized));
});

test("jobFilePath uses <repo>/<workDir>/jobs/<taskId>.json", () => {
  const path = jobFilePath("/tmp/myrepo", "t_abc", ".cc-delegate");
  // join normalises slashes per platform; check the trailing structure instead
  // so this works on both Windows and POSIX test runners.
  assert.ok(path.endsWith(join("myrepo", ".cc-delegate", "jobs", "t_abc.json")));
});

test("saveJob writes to <repo>/<workDir>/jobs/<taskId>.json and creates dirs recursively", async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "cctest-persist-"));
  t.after(() => rm(dir, { recursive: true, force: true }));

  const repo = join(dir, "nested", "deeper", "repo");
  const job = makeJob({ repo });
  const writtenPath = await saveJob(job, ".cc-delegate");
  const expectedPath = jobFilePath(repo, job.taskId, ".cc-delegate");
  assert.equal(writtenPath, expectedPath);

  const raw = await readFile(expectedPath, "utf8");
  const parsed = JSON.parse(raw);
  assert.equal(parsed.taskId, "t_persist123");
  assert.equal(parsed.status, "running");
  // AbortController instance must never reach disk.
  assert.equal(parsed.abort, undefined);
  assert.ok(!raw.includes("AbortController"));
});

test("saveJob -> loadJob round-trip preserves all non-abort fields through disk", async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "cctest-persist-"));
  t.after(() => rm(dir, { recursive: true, force: true }));

  const repo = join(dir, "repo");
  const job = makeJob({
    repo,
    status: "failed",
    error: "uv was not found",
    turns: 3,
    progress: "halfway through implementer",
  });
  await saveJob(job, ".cc-delegate");
  const loaded = await loadJob(repo, job.taskId, ".cc-delegate");
  assert.ok(loaded, "expected loadJob to return the persisted job");
  assert.equal(loaded.taskId, job.taskId);
  assert.equal(loaded.status, "failed");
  assert.equal(loaded.error, "uv was not found");
  assert.equal(loaded.turns, 3);
  assert.equal(loaded.progress, "halfway through implementer");
  assert.equal(loaded.branch, "delegate/t_persist123");
  assert.equal(loaded.worktree, "/tmp/worktrees/t_persist123");
  assert.equal(loaded.abort, undefined);
});

test("loadJob returns null for a missing file (does not throw)", async () => {
  const loaded = await loadJob("/nonexistent/repo/path", "t_does_not_exist");
  assert.equal(loaded, null);
});

test("deletePersistedJob removes the persisted file", async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "cctest-persist-"));
  t.after(() => rm(dir, { recursive: true, force: true }));

  const repo = join(dir, "repo");
  const job = makeJob({ repo });
  await saveJob(job, ".cc-delegate");
  await deletePersistedJob(job, ".cc-delegate");
  // The file should now be gone.
  const reloaded = await loadJob(repo, job.taskId, ".cc-delegate");
  assert.equal(reloaded, null);
});

test("deletePersistedJob is a no-op when the file is already gone", async () => {
  const job = makeJob({ repo: "/nonexistent/repo/path" });
  // Must not throw ENOENT or anything else.
  await deletePersistedJob(job, ".cc-delegate");
});

test("rememberRepo populates the known-repos registry so findPersistedJob can locate restored jobs", async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "cctest-persist-"));
  t.after(() => rm(dir, { recursive: true, force: true }));

  // Use a unique task id so we don't collide with other tests sharing module state.
  const taskId = `t_repo_lookup_${Date.now().toString(36)}`;
  const repo = join(dir, "repo");
  const job = { ...makeJob({ repo }), taskId };

  rememberRepo(repo);
  await saveJob(job, ".cc-delegate");

  // loadJob alone needs the repo; findPersistedJob (which the MCP server's
  // getJobWithFallback uses) iterates the known-repos set instead.
  const { findPersistedJob } = await import("./build/persistence.js");
  const restored = await findPersistedJob(taskId, ".cc-delegate");
  assert.ok(restored);
  assert.equal(restored.taskId, taskId);
  assert.equal(restored.repo, repo);
  assert.equal(restored.abort, undefined);
});