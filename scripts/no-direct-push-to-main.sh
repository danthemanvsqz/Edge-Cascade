#!/usr/bin/env bash
# Block a direct `git push` to the protected branch (main/master).
#
# This is the LOCAL half of a two-layer gate. It catches the common
# accident -- pushing straight to main from a dev box -- before it leaves
# the machine, and points you at the PR flow instead. It is advisory:
# `--no-verify`, the web UI, and other clones bypass it, so the
# AUTHORITATIVE gate is GitHub branch protection on `main` (see
# CONTRIBUTING / the repo settings; this hook does not replace it).
#
# Wired as a pre-push hook (.pre-commit-config.yaml, stages: [pre-push]).
# pre-commit (>=4) exports the push destination as PRE_COMMIT_REMOTE_BRANCH;
# we only block when we can POSITIVELY identify it as the protected branch,
# so feature-branch pushes are never collateral damage.
#
# Deliberate escape hatch for the rare legitimate case (loud on purpose):
#   ALLOW_DIRECT_MAIN_PUSH=1 git push origin main
set -euo pipefail

protected_re='^refs/heads/(main|master)$'
target="${PRE_COMMIT_REMOTE_BRANCH:-}"

# Not pushing to the protected branch (or destination unknowable) -> allow.
[[ "$target" =~ $protected_re ]] || exit 0

if [[ "${ALLOW_DIRECT_MAIN_PUSH:-}" == "1" ]]; then
  echo "no-direct-push-to-main: ALLOW_DIRECT_MAIN_PUSH=1 set -- permitting" \
       "direct push to ${target#refs/heads/}. This bypasses the PR flow." >&2
  exit 0
fi

remote="${PRE_COMMIT_REMOTE_NAME:-origin}"
cat >&2 <<EOF

  ✖ BLOCKED: direct push to '${target#refs/heads/}' (-> ${remote}).

  main is protected. Land changes through a pull request:

      git switch -c feat/<topic>
      git push -u ${remote} feat/<topic>
      # open a PR into main on GitHub

  This local hook is defense-in-depth; ask an admin to enable GitHub
  branch protection on main for the authoritative, unbypassable gate.

  Emergency override (knowingly skips the PR flow):
      ALLOW_DIRECT_MAIN_PUSH=1 git push ${remote} ${target#refs/heads/}

EOF
exit 1
