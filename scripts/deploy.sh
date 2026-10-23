#!/usr/bin/env bash
# Deploy script — deploys the built artifact to the target environment
#
# Usage: ./scripts/deploy.sh [env]
# Example: ./scripts/deploy.sh staging

set -euo pipefail

ENV="${1:-staging}"
cd "$(dirname "$0")/.."

echo "==> Deploying to ${ENV}..."
# Add your deployment steps here, e.g.:
# - Upload to artifact registry
# - Trigger cloud deployment
# - Run migration scripts
echo "==> Deployment to ${ENV} complete (placeholder)."
