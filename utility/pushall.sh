#!/bin/bash

# Default commit message if none provided
MSG="${1:-Auto-commit from Pi}"

# Add all changes
sudo git add .

# Commit
sudo git commit -m "$MSG"

# Pull latest to avoid conflicts
sudo git pull --rebase origin main

# Push to GitHub
sudo git push origin main
