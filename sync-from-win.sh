#!/usr/bin/env bash
set -e

git checkout mac/minsuk
git fetch origin
git rebase origin/win/minsuk
