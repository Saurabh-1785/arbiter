#!/bin/bash
# ARBITER — Project Restructuring Script
# Renames src/ → backend/, removes old dashboard, updates all imports.
# Run from the arbiter/ root directory.

set -e

echo "=== ARBITER Restructuring ==="

# 1. Rename src/ to backend/
echo "[1/5] Renaming src/ → backend/"
mv src backend

# 2. Remove old legacy dashboard (replaced by React frontend)
echo "[2/5] Removing old legacy dashboard files"
rm -rf backend/dashboard

# 3. Update all Python imports: src. → backend.
echo "[3/5] Updating Python imports (src. → backend.)"
find backend tests scripts -name '*.py' -exec sed -i 's/from src\./from backend./g' {} +
find backend tests scripts -name '*.py' -exec sed -i 's/import src\./import backend./g' {} +

# 4. Update pyproject.toml
echo "[4/5] Updating pyproject.toml"
sed -i 's|pythonpath = \["\."\]|pythonpath = ["."]|' pyproject.toml

# 5. Update verifier_service.py dashboard path
echo "[5/5] Updating verifier_service.py paths"
# The path resolution in verifier_service.py uses __file__ relative paths,
# so it adjusts automatically. But we need to update the _PROJECT_ROOT.
# __file__ is backend/verifier/verifier_service.py
# parent.parent.parent goes: verifier → backend → arbiter (correct!)
# So _PROJECT_ROOT is still correct.

echo ""
echo "=== Structure after restructuring ==="
echo ""
find . -maxdepth 3 -not -path './.venv/*' -not -path './.git/*' -not -path './.pytest_cache/*' -not -path './frontend/node_modules/*' -not -name '__pycache__' -not -name '*.pyc' | sort | head -60

echo ""
echo "=== Done! ==="
echo "Run: pytest tests/ -v  (to verify nothing broke)"
echo "Run: cd frontend && npm install && npm run build && cd .."
echo "Run: python scripts/run_demo.py"
