#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_ROOT}"

echo ""
echo "========================================"
echo "Opportunity Discovery Engine Setup"
echo "========================================"
echo ""

# -----------------------------------------------------------------------------
# Corporate CA
# -----------------------------------------------------------------------------

echo "[postCreate] Checking corporate TLS..."

if [ -f "${SCRIPT_DIR}/install-corp-ca.sh" ]; then
  sudo -E sh "${SCRIPT_DIR}/install-corp-ca.sh"
fi

# -----------------------------------------------------------------------------
# Verify Core Tooling
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Tool versions"
echo ""

node -v
npm -v
python3 --version
gh --version | head -n 1
devin --version || true

# -----------------------------------------------------------------------------
# PNPM
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Installing PNPM..."
echo ""

npm install -g pnpm

# -----------------------------------------------------------------------------
# Playwright
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Installing Playwright browsers..."
echo ""

npx playwright install || true

# -----------------------------------------------------------------------------
# Python Environment
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Python environment..."
echo ""

python3 -m venv .venv || true

# -----------------------------------------------------------------------------
# Matt Pocock Skills
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Installing skills..."
echo ""

npx --yes skills add https://github.com/mattpocock/skills \
  --agent codex \
  --skill setup-matt-pocock-skills \
  --skill grill-with-docs \
  --skill to-spec \
  --skill to-tickets \
  --skill tdd \
  --skill code-review \
  --skill implement \
  --skill triage \
  --yes

if [ -d .claude/skills ]; then
  find .claude/skills -maxdepth 1 -type l -delete
  rmdir --ignore-fail-on-non-empty .claude/skills .claude 2>/dev/null || true
fi

# -----------------------------------------------------------------------------
# Create ODE Workspace Structure
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Creating workspace structure..."
echo ""

mkdir -p app/pages
mkdir -p src/ode
mkdir -p tests/fixtures
mkdir -p docs
mkdir -p data
mkdir -p reports

# -----------------------------------------------------------------------------
# Git Hooks Support
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Configuring Git..."
echo ""

git config --global pull.rebase false

# -----------------------------------------------------------------------------
# Display Installed Skills
# -----------------------------------------------------------------------------

echo ""
echo "[postCreate] Installed skills"
echo ""

ls -1 .agents/skills 2>/dev/null || true

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

echo ""
echo "========================================"
echo "SETUP COMPLETE"
echo "========================================"
echo "
