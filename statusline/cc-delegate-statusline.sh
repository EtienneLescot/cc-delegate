#!/usr/bin/env bash
# cc-delegate status line — token-free.
#
# All the rendering happens in the MCP server (Python), which writes a
# pre-baked line to ~/.cc-delegate/statusline. This reader just prints it
# while it is fresh. No jq, no python, no JSON parsing here on purpose:
# the fewer dependencies, the more reliably it runs in Claude Code's
# status-line shell (Git Bash on Windows, sh elsewhere).
#
# File format:  line 1 = expiry epoch, line 2 = the rendered status line.
#
# Wire it in ~/.claude/settings.json (see the plugin README):
#   "statusLine": { "type": "command",
#                   "command": "~/.claude/cc-delegate-statusline.sh",
#                   "refreshInterval": 2 }
# refreshInterval is REQUIRED: status-line event triggers go quiet while the
# session waits on the background worker, so the timer is what keeps it live.

cat >/dev/null 2>&1  # drain the JSON on stdin; we don't need it

f="$HOME/.cc-delegate/statusline"
[ -f "$f" ] || exit 0

until_epoch=$(head -1 "$f" 2>/dev/null)
case "$until_epoch" in
  ''|*[!0-9]*) exit 0 ;;   # missing/non-numeric guard
esac

now=$(date +%s)
if [ "$now" -le "$until_epoch" ]; then
  tail -n +2 "$f"
fi
exit 0
