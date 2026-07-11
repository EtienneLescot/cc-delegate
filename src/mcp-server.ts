#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { loadConfig } from "./config.js";
import { cleanupJob, createWorktree, getJob, getJobWithFallback, persistJob, putJob, type Job } from "./jobs.js";
import { runWorker } from "./worker.js";

const cfg = loadConfig();
const server = new McpServer({ name: "cc-delegate", version: "0.2.0" });

server.registerTool(
  "run_dev_task",
  {
    title: "Delegate a heavy dev task to the worker model",
    description: "Starts an autonomous coding worker on an isolated git worktree. Returns a task_id immediately.",
    inputSchema: {
      spec: z.string().describe("Objective and constraints in natural language"),
      repo_path: z.string().describe("Absolute path to the target git repository"),
      test_command: z.string().optional(),
      definition_of_done: z.string().optional(),
      base_branch: z.string().optional(),
      recursion_limit: z.number().optional().describe("LangGraph step budget"),
      timeout_ms: z.number().optional(),
    },
  },
  async (a) => {
    const { taskId, branch, worktree, repo } = await createWorktree(cfg, a.repo_path, a.base_branch);
    const abort = new AbortController();
    const timeout = a.timeout_ms ?? cfg.defaultTimeoutMs;
    const timer = setTimeout(() => abort.abort(), timeout);
    const job: Job = { taskId, status: "running", turns: 0, costUsd: null, totalTokens: null, branch, worktree, repo, abort };
    putJob(job);
    void persistJob(job, cfg);

    // Fire-and-forget: the worker runs in the background; job state is
    // updated live by worker.ts via persistJob() on every change.
    void runWorker(cfg, {
      spec: a.spec, worktree, taskId,
      testCommand: a.test_command, definitionOfDone: a.definition_of_done,
      recursionLimit: a.recursion_limit ?? cfg.defaultRecursionLimit,
      rubricMaxIterations: cfg.defaultRubricMaxIterations,
    }).finally(() => clearTimeout(timer));

    return {
      content: [{ type: "text", text: JSON.stringify({ task_id: taskId, status: "running", branch, worktree }) }],
    };
  }
);

server.registerTool(
  "get_task_status",
  { title: "Poll a delegated task", description: "Returns current status, progress, cost and turns.", inputSchema: { task_id: z.string() } },
  async ({ task_id }) => {
    const j = (await getJobWithFallback(task_id, cfg)) ?? getJob(task_id);
    if (!j) return { content: [{ type: "text", text: JSON.stringify({ error: "unknown task_id" }) }], isError: true };
    return { content: [{ type: "text", text: JSON.stringify({
      task_id, status: j.status, progress: j.progress, turns: j.turns,
      cost_usd: j.costUsd, total_tokens: j.totalTokens, error: j.error,
    }) }] };
  }
);

server.registerTool(
  "fetch_task_result",
  { title: "Fetch the result of a completed task", description: "Returns summary, patch, files changed, tests and cost.", inputSchema: { task_id: z.string() } },
  async ({ task_id }) => {
    const j = (await getJobWithFallback(task_id, cfg)) ?? getJob(task_id);
    if (!j) return { content: [{ type: "text", text: JSON.stringify({ error: "unknown task_id" }) }], isError: true };
    return { content: [{ type: "text", text: JSON.stringify({
      task_id, status: j.status, summary: j.summary, patch_path: j.patchPath,
      files_changed: j.filesChanged ?? [], tests: j.tests ?? {},
      cost_usd: j.costUsd, total_tokens: j.totalTokens, num_turns: j.turns,
      branch: j.branch, worktree: j.worktree, error: j.error,
    }) }] };
  }
);

server.registerTool("cleanup_task", { title: "Tear down a finished task", description: "Removes the worktree, branch, and persisted file for a finished task.", inputSchema: { task_id: z.string(), delete_branch: z.boolean().optional() } },
  async ({ task_id, delete_branch }) => {
    const j = (await getJobWithFallback(task_id, cfg)) ?? getJob(task_id);
    if (!j) return { content: [{ type: "text", text: JSON.stringify({ error: "unknown task_id" }) }], isError: true };
    if (j.status === "running") {
      return { content: [{ type: "text", text: JSON.stringify({ task_id, error: "task is still running; abort or wait before calling cleanup_task" }) }], isError: true };
    }
    const result = await cleanupJob(cfg, j, { deleteBranch: delete_branch ?? true });
    return { content: [{ type: "text", text: JSON.stringify({ task_id, cleaned: true, ...result }) }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
