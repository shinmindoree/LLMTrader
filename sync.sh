#!/usr/bin/env bash


if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ 커밋 안 된 변경 있음. 먼저 commit 하세요."
  exit 1
fi


./sync-from-win.sh
