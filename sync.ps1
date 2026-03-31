
git diff --quiet
$dirty1 = $LASTEXITCODE

git diff --cached --quiet
$dirty2 = $LASTEXITCODE

if ($dirty1 -ne 0 -or $dirty2 -ne 0) {
  Write-Host "Uncommitted changes detected. Please commit first."
  exit 1
}

git checkout win/minsuk
git fetch origin
git rebase origin/mac/minsuk
