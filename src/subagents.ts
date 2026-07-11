import type { AgentDefinition } from "@anthropic-ai/claude-agent-sdk";

// model omis => hérite de l'endpoint MiniMax du worker.
export const workerAgents: Record<string, AgentDefinition> = {
  implementer: {
    description: "Writes and edits source code to satisfy the spec. Use for implementation work.",
    tools: ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    prompt:
      "You implement the requested change with minimal, focused edits. " +
      "Follow the repository's conventions (see CLAUDE.md if present). " +
      "Never touch files outside the working directory. Do not run git push, merge, or destructive commands.",
  },
  tester: {
    description: "Writes and runs tests, and reports failures. Use to validate the implementation.",
    tools: ["Read", "Write", "Bash", "Glob", "Grep"],
    prompt:
      "You write and run tests only. Do not modify non-test source files. " +
      "Run the provided test command, summarize pass/fail, and return the failing output tail.",
  },
  reviewer: {
    description: "Read-only reviewer that returns a prioritized list of issues.",
    tools: ["Read", "Grep", "Glob"],
    prompt:
      "You review changes for correctness, security, and adherence to the spec. " +
      "Return a prioritized, actionable list. You never edit files.",
  },
};
