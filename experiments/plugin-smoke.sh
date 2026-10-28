#!/bin/zsh
# End-to-end smoke test of the plugin in a throwaway repo. Never touches another project.
# Usage: plugin-smoke.sh <workdir> [--plugin-dir <dir> | --marketplace]
#   --plugin-dir D  load the plugin from folder D (claude --plugin-dir)
#   --marketplace   install buildkit@buildkit (local scope, this throwaway repo only) from the
#                   marketplace already added with: claude plugin marketplace add <folder or repo>
# Each step is one headless desk session (claude -p, Sonnet). Output lands in <workdir>/out.
set -u
W=${1:?workdir}; shift
PLUG=()
[[ "${1:-}" == "--plugin-dir" ]] && PLUG=(--plugin-dir "$2")
rm -rf "$W"; mkdir -p "$W/out"
R="$W/proj"; REMOTE="$W/remote.git"
git init -q --bare -b main "$REMOTE"
git init -q -b main "$R"; cd "$R"
git config user.name smoke; git config user.email smoke@test; git config commit.gpgsign false
cat > calc.py <<'PY'
def add(a, b):
    return a - b
PY
cat > test_calc.py <<'PY'
import unittest
from calc import add

class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

if __name__ == "__main__":
    unittest.main()
PY
git add .; git commit -qm init; git remote add origin "$REMOTE"; git push -q -u origin main
mkdir build
cat > build/config.yml <<'YML'
language: "Python 3, stdlib unittest"
test_command: "python3 -m unittest discover -s . -p 'test_*.py'"
key_paths:
  - calc.py
  - test_calc.py
protected:
  - .env
main_branch: main
remote: origin
ship: push
YML
if [[ "${1:-}" == "--marketplace" ]]; then
  claude plugin install buildkit@buildkit --scope local > "$W/out/install.txt" 2>&1 || { cat "$W/out/install.txt"; exit 1; }
fi
ALLOW='Agent,Read,Grep,Glob,Edit,Write,Bash(python3:*),Bash(git:*),Bash(cd:*),Bash(ls:*),Bash(mkdir:*),Bash(cat:*),Bash(pwd:*)'
desk() {  # desk <name> <prompt>
  local n=$1; shift
  echo "$*" | claude -p --model sonnet "${PLUG[@]}" --output-format json --permission-mode acceptEdits \
    --allowedTools "$ALLOW" > "$W/out/$n.json" 2> "$W/out/$n.err"
  echo "== $n exit=$?"; python3 -c "import json;d=json.load(open('$W/out/$n.json'));print(d.get('session_id'));print(str(d.get('result'))[:600])"
}
KIT=${KIT:-python3 $HOME/Code/buildkit/kit/kit.py}
desk idea '/buildkit:idea calc.add returns a - b. Fix it so add returns a + b. One line change in calc.py, test_calc.py already checks it. Size small, risk none.'
$=KIT queue; $=KIT board
desk run '/buildkit:run 1'
echo "== state after run"; $=KIT status 1; $=KIT queue
desk ship '/buildkit:ship 1'
echo "== after ship"; $=KIT status 1; git -C "$R" log --oneline | head -3; git --git-dir "$REMOTE" log --oneline main | head -3
git -C "$R" worktree list; git -C "$R" branch -a
cat calc.py
