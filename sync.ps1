if (-not (git diff --quiet) -or -not (git diff --cached --quiet)) {
  Write-Host "Uncommitted changes detected. Please commit first."
  exit 1
}

git checkout win/minsuk
git fetch origin
git rebase origin/mac/minsuk