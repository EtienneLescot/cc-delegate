#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { loadConfig } from "./config.js";
import { createWorktree, getJob, putJob, type Job } from "./jobs.js";
import { runWorker } from "./worker.js";

const cfg = loadConfig();
const server = new McpServer({ name: "cc-delegate", version: "0.1.0" });

server.registerTool(
  "run_dev_task",
  {
    title: "Delegate a heavy dev task to the worker model",
    description:
      "Starts an autonomous coding worker (on whatever model DELEGATE_MODEL points to) on an isolated " +
      "git worktree. Returns a task_id immediately; poll with get_task_status, then fetch_task_result.",
    inputSchema: {
      spec: z.string().describe("Objective and constraints in natural language"),
      repo_path: z.string().describe("Absolute path to the target git repository"),
      test_command: z.string().optional(),
      definition_of_done: z.string().optional(),
      base_branch: z.string().optional(),
      max_turns: z.number().optional(),
      max_budget_usd: z.number().optional(),
      timeout_ms: z.number().optional(),
    },
  },
  async (a) => {
    const { taskId, branch, worktree, repo } = await createWorktree(cfg, a.repo_path, a.base_branch);
    const abort = new AbortController();
    const timeout = a.timeout_ms ?? cfg.defaultTimeoutMs;
    const timer = setTimeout(() => abort.abort(), timeout);
    const job: Job = { taskId, status: "running", turns: 0, costUsd: 0, branch, worktree, repo, abort };
    putJob(job);

    // Fire-and-forget : le worker tourne en fond, le job est mis à jour au fil de l'eau.
    runWorker(cfg, {
      spec: a.spec, worktree, taskId,
      testCommand: a.test_command, definitionOfDone: a.definition_of_done,
      maxTurns: a.max_turns ?? cfg.defaultMaxTurns,
      maxBudgetUsd: a.max_budget_usd ?? cfg.defaultMaxBudgetUsd,
    }).finally(() => clearTimeout(timer));

    return {
      content: [{ type: "text", text: JSON.stringify({ task_id: taskId, status: "running", branch, worktree }) }],
    };
  }
);

server.registerTool(
  "get_task_status",
  { title: "Poll a delegated task", description: "Returns current status, progress, cost and turns.",
    inputSchema: { task_id: z.string() } },
  async ({ task_id }) => {
    const j = getJob(task_id);
    if (!j) return { content: [{ type: "text", text: JSON.stringify({ error: "unknown task_id" }) }], isError: true };
    return { content: [{ type: "text", text: JSON.stringify({
      task_id, status: j.status, progress: j.progress, turns: j.turns, cost_usd: j.costUsd, error: j.error,
    }) }] };
  }
);

server.registerTool(
  "fetch_task_result",
  { title: "Fetch the result of a completed task",
    description: "When status is 'succeeded', returns summary, patch path, files changed, tests and cost.",
    inputSchema: { task_id: z.string() } },
  async ({ task_id }) => {
    const j = getJob(task_id);
    if (!j) return { content: [{ type: "text", text: JSON.stringify({ error: "unknown task_id" }) }], isError: true };
    return { content: [{ type: "text", text: JSON.stringify({
      task_id, status: j.status, summary: j.summary, patch_path: j.patchPath,
      files_changed: j.filesChanged ?? [], tests: j.tests ?? {},
      cost_usd: j.costUsd, num_turns: j.turns, branch: j.branch, worktree: j.worktree, error: j.error,
    }) }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
