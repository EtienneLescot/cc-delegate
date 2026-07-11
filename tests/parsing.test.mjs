// Unit tests for the pure line-parsing helpers exported from src/persistence.ts.
// These helpers are also exercised indirectly by the live worker, but having a
// fast feedback loop on the helpers themselves makes regressions cheap to spot.
//
// The tests import from tests/build/persistence.js, a tiny esbuild bundle that
// the `build:test-helpers` npm script produces from src/persistence.ts before
// `npm test` runs `node --test tests/`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  findLastResultLine,
  parseProgressLine,
  progressNote,
  stripResultMarker,
} from "./build/persistence.js";

test("findLastResultLine returns null on empty stdout", () => {
  assert.equal(findLastResultLine(""), null);
});

test("findLastResultLine returns null when no RESULT_JSON line is present", () => {
  const stdout = [
    "starting up",
    "PROGRESS:{\"step\":1,\"node\":\"start\"}",
    "still working",
    "RESULT_json:lowercase does not count",
  ].join("\n");
  // Only the exact prefix matches — different casings, missing colon, etc. do not.
  assert.equal(findLastResultLine(stdout), null);
});

test("findLastResultLine extracts the last line that starts with RESULT_JSON:", () => {
  const stdout = [
    "garbage line",
    "PROGRESS:{\"step\":1,\"node\":\"start\",\"note\":\"first\"}",
    "more garbage",
    "PROGRESS:{\"step\":2,\"node\":\"work\",\"note\":\"second\"}",
    "even more garbage",
    "PROGRESS:{\"step\":3,\"node\":\"done\",\"note\":\"finished\"}",
    "RESULT_JSON:{\"status\":\"succeeded\",\"summary\":\"all done\",\"turns\":7,\"error\":null,\"rubric_status\":\"satisfied\",\"cost_usd\":0.42,\"total_tokens\":12345}",
    "trailing garbage that should be ignored",
  ].join("\n");
  const line = findLastResultLine(stdout);
  assert.ok(line, "expected to find a result line");
  assert.ok(line.startsWith("RESULT_JSON:"));
  const parsed = JSON.parse(stripResultMarker(line));
  assert.equal(parsed.status, "succeeded");
  assert.equal(parsed.summary, "all done");
  assert.equal(parsed.turns, 7);
  assert.equal(parsed.cost_usd, 0.42);
  assert.equal(parsed.total_tokens, 12345);
});

test("findLastResultLine picks the last RESULT_JSON line when several are emitted", () => {
  const stdout = [
    "PROGRESS:{\"step\":1,\"node\":\"start\"}",
    "RESULT_JSON:{\"status\":\"failed\",\"turns\":1}",
    "some intermediate output",
    "RESULT_JSON:{\"status\":\"succeeded\",\"turns\":2}",
    "PROGRESS:{\"step\":3,\"node\":\"end\"}",
  ].join("\n");
  const line = findLastResultLine(stdout);
  assert.ok(line);
  const parsed = JSON.parse(stripResultMarker(line));
  assert.equal(parsed.status, "succeeded");
  assert.equal(parsed.turns, 2);
});

test("findLastResultLine treats CRLF line endings the same as LF", () => {
  const stdout =
    "PROGRESS:{\"step\":1,\"node\":\"start\"}\r\n" +
    "RESULT_JSON:{\"status\":\"succeeded\",\"turns\":1}\r\n" +
    "trailing";
  const line = findLastResultLine(stdout);
  assert.ok(line);
  const parsed = JSON.parse(stripResultMarker(line));
  assert.equal(parsed.status, "succeeded");
});

test("stripResultMarker strips the prefix only when present", () => {
  assert.equal(stripResultMarker('RESULT_JSON:{"a":1}'), '{"a":1}');
  assert.equal(stripResultMarker("hello"), "hello");
  assert.equal(stripResultMarker(""), "");
});

test("parseProgressLine returns null for non-PROGRESS lines", () => {
  assert.equal(parseProgressLine("hello"), null);
  assert.equal(parseProgressLine(""), null);
  assert.equal(parseProgressLine("RESULT_JSON:{\"status\":\"succeeded\"}"), null);
  assert.equal(parseProgressLine("PROGRESS"), null);
  assert.equal(parseProgressLine("PROGRESS:"), null);
});

test("parseProgressLine parses well-formed PROGRESS lines", () => {
  assert.deepEqual(
    parseProgressLine('PROGRESS:{"step":3,"node":"reviewer","note":"checking tests"}'),
    { step: 3, node: "reviewer", note: "checking tests" },
  );
  // Partial payloads are fine: every field is optional.
  assert.deepEqual(parseProgressLine('PROGRESS:{"step":2}'), { step: 2 });
  assert.deepEqual(parseProgressLine('PROGRESS:{"node":"only"}'), { node: "only" });
});

test("parseProgressLine returns null for malformed JSON", () => {
  assert.equal(parseProgressLine("PROGRESS:not-json"), null);
  assert.equal(parseProgressLine("PROGRESS:{"), null);
  assert.equal(parseProgressLine("PROGRESS:null"), null);
  assert.equal(parseProgressLine("PROGRESS:[1,2,3]"), null);
  assert.equal(parseProgressLine("PROGRESS:42"), null);
});

test("progressNote prefers an explicit note when present", () => {
  assert.equal(
    progressNote({ step: 1, node: "reviewer", note: "first run" }),
    "first run",
  );
});

test("progressNote falls back to node#step when note is missing", () => {
  assert.equal(progressNote({ step: 2, node: "reviewer" }), "reviewer#2");
  // Node wins even when step is missing too.
  assert.equal(progressNote({ node: "implementer" }), "implementer#?");
});

test("progressNote falls back to 'step N' when only step is provided", () => {
  assert.equal(progressNote({ step: 4 }), "step 4");
  assert.equal(progressNote({}), "step ?");
});

test("end-to-end: extract RESULT_JSON from mixed stdout with garbage and PROGRESS lines", () => {
  // Simulates what the Python worker will emit over the course of a run.
  const stdout = [
    "Loading environment...",
    'PROGRESS:{"step":1,"node":"planner","note":"reading spec"}',
    "litellm.acompletion: routing to litellm:minimax/MiniMax-M3",
    'PROGRESS:{"step":2,"node":"implementer","note":"editing src/foo.ts"}',
    "deepagents.backends.local_shell: virtual_mode=True",
    'PROGRESS:{"step":3,"node":"tester","note":"running pytest"}',
    "tests/test_foo.py::test_bar PASSED",
    "tests/test_foo.py::test_baz FAILED",
    "pytest exit code 1",
    'PROGRESS:{"step":4,"node":"implementer","note":"fixing failing test"}',
    "pytest exit code 0",
    'RESULT_JSON:{"status":"succeeded","summary":"all tests green","turns":42,"error":null,"rubric_status":"satisfied","cost_usd":null,"total_tokens":null}',
    "shutdown complete",
  ].join("\n");

  const line = findLastResultLine(stdout);
  assert.ok(line, "expected to find a RESULT_JSON line");
  const parsed = JSON.parse(stripResultMarker(line));
  assert.equal(parsed.status, "succeeded");
  assert.equal(parsed.summary, "all tests green");
  assert.equal(parsed.turns, 42);
  assert.equal(parsed.rubric_status, "satisfied");
});