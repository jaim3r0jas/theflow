#!/usr/bin/env bash
# theflow gh-check [dir]: make sure gh works in a directory before a session relies on it, fixing what can be fixed
# without the user:
#   installed   the gh on PATH runs there. With asdf, a repo's .tool-versions may pin a github-cli version that is not
#               installed ("No version is set for command gh"): install that version.
#   git         https pushes may go through `gh auth git-credential` at a fixed path in ~/.gitconfig; if that path
#               is gone (a gh version removed), point it at this gh again (`gh auth setup-git`).
#   logged in   the active github.com account has a valid token, and can read the directory's GitHub repo (its
#               origin) if it has one. An expired or missing login needs the user's browser, so it is never done
#               here: print the command for the user and exit 2.
# Prints "gh ok: ..." or what is wrong and the fix. Exit 0 ok, 1 gh not installed, 2 login needed.
# Default dir: the current directory (a worktree, a base or the hub).
set -uo pipefail

LOGIN_CMD='! gh auth login -h github.com -p https -w'

DIR=$(cd "${1:-$PWD}" 2>/dev/null && pwd -P) || { echo "gh-check: no such directory: ${1:-}"; exit 1; }
cd "$DIR"

# nearest github-cli pin in a .tool-versions, walking up from DIR (asdf's own lookup, ending at ~/.tool-versions)
pinned() {
  local d=$DIR v
  while :; do
    if [ -f "$d/.tool-versions" ]; then
      v=$(awk '$1 == "github-cli" { print $2; exit }' "$d/.tool-versions")
      [ -n "$v" ] && { echo "$v"; return; }
    fi
    [ "$d" = / ] && break
    d=$(dirname "$d")
  done
  awk '$1 == "github-cli" { print $2; exit }' ~/.tool-versions 2>/dev/null
}

if ! command -v gh >/dev/null; then
  echo "gh: not on PATH. Install it: https://cli.github.com (brew install gh, or asdf install github-cli latest)"
  exit 1
fi
if ! gh --version >/dev/null 2>&1; then
  v=$(pinned)
  if [ -n "$v" ] && command -v asdf >/dev/null; then
    echo "gh: github-cli $v (pinned for $DIR) is not installed; installing it with asdf"
    asdf install github-cli "$v" >/dev/null 2>&1
  fi
  if ! gh --version >/dev/null 2>&1; then
    echo "gh: does not run in $DIR:"
    gh --version 2>&1 | head -3 | sed 's/^/  /'
    exit 1
  fi
fi
version=$(gh --version | awk 'NR == 1 { print $3 }')

helper=$(git config --global --get-all credential.https://github.com.helper 2>/dev/null | sed -n 's/^!\([^ ]*\) auth git-credential.*/\1/p' | tail -1)
if [ -n "$helper" ] && [ ! -x "$helper" ]; then
  gh auth setup-git -h github.com && echo "gh: git's credential helper pointed to a removed gh ($helper); set it to this one"
fi

if ! gh auth status -h github.com --active >/dev/null 2>&1 || ! login=$(gh api user -q .login 2>/dev/null); then
  echo "gh: not logged in to github.com, or the login expired. Ask the user to run (it opens the browser):"
  echo "  $LOGIN_CMD"
  exit 2
fi

gh_repo() { git -C "$1" remote get-url origin 2>/dev/null | sed -nE 's#^.*github\.com[:/]([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//'; }
repo=$(gh_repo .)
if [ -z "$repo" ] && [ -f flow.json ]; then   # the hub root: probe with its first GitHub base
  for b in bases/*/; do repo=$(gh_repo "$b"); [ -n "$repo" ] && break; done
fi
if [ -n "$repo" ] && ! gh api "repos/$repo" -q .full_name >/dev/null 2>&1; then
  echo "gh: logged in as $login, which can't read $repo. Ask the user to switch to an account that can"
  echo "  (gh auth switch -h github.com -u <account>) or log in with it: $LOGIN_CMD"
  exit 2
fi

echo "gh ok: $version as $login in $DIR${repo:+ ($repo readable)}"
