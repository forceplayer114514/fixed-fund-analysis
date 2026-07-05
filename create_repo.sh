#!/bin/bash
export PATH="/opt/homebrew/bin:$PATH"

echo "Checking GitHub authentication..."
gh auth status > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "You are not logged into GitHub CLI. Please run:"
    echo "/opt/homebrew/bin/gh auth login"
    exit 1
fi

echo "Creating public repository 'fixed_fund_analysis' on GitHub..."
gh repo create fixed_fund_analysis --public --source=. --remote=origin --push

if [ $? -eq 0 ]; then
    echo "✅ Successfully created and pushed to https://github.com/$(gh api user -q .login)/fixed_fund_analysis"
else
    echo "❌ Failed to create/push repository. See error above."
fi
