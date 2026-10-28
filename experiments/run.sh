#!/bin/zsh
# Usage: run.sh <stripped|default> <n>
# Headless parent session delegates to one bkexp subagent. Output goes to $OUT (default ./runs).
AGENT=$1; N=$2; OUT=${OUT:-./runs}
mkdir -p "$OUT"; cd "${RUNDIR:-$HOME/Code/buildkit}" || exit 1
TASK="Task: in $HOME/Code/poker-game (read-only, change nothing) name the command used to run the tests and count the test files. Answer in under 5 lines."
S=$(date +%s)
claude -p --model sonnet --plugin-dir "$HOME/Code/buildkit/experiments" --add-dir "$HOME/Code/poker-game" \
  --output-format json \
  "Use the Agent tool exactly once with subagent_type \"bkexp:$AGENT\" and pass it this prompt verbatim: $TASK Then print the subagent's reply and nothing else." \
  > "$OUT/$AGENT-$N.json" 2> "$OUT/$AGENT-$N.err"
echo "wall_s=$(( $(date +%s) - S ))" >> "$OUT/$AGENT-$N.err"
