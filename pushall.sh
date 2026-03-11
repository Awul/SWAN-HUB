#!/bin/bash

# Default commit message if none provided
MSG="${1:-Auto-commit from Pi}"

# Add all changes
git add .

# Commit
git commit -m "$MSG"

# Pull latest to avoid conflicts
git pull --rebase origin main

# Push to GitHub
git push origin main
