#!/bin/bash
# AUREA Orchestrator - Task Enqueue Helper
# Usage: ./enqueue.sh <task_type> [options]

set -euo pipefail

# Configuration
API_URL="${AUREA_API_URL:-https://aurea-orchestrator-api.onrender.com}"
API_KEY="${AUREA_API_KEY}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper functions
error() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

success() {
    echo -e "${GREEN}Success: $1${NC}"
}

info() {
    echo -e "${YELLOW}Info: $1${NC}"
}

usage() {
    cat << EOF
Usage: $0 <task_type> [options]

Task Types:
  code_pr       - Create GitHub PR with AI-generated code
  centerpoint   - Sync CenterPoint data
  mrg_deploy    - Deploy MyRoofGenius service
  gen_content   - Generate content using AI
  aurea_action  - Execute AUREA composite workflow

Options:
  -p, --payload <json>     Task payload (JSON string)
  -f, --file <path>        Read payload from file
  --priority <level>       Priority: critical, high, normal, low (default: normal)
  --idempotency <key>      Idempotency key to prevent duplicates
  --api-url <url>          Override API URL
  --api-key <key>          Override API key
  -h, --help               Show this help message

Examples:
  # Generate content
  $0 gen_content -p '{"prompt": "Write a haiku about distributed systems"}'
  
  # Create PR from file
  $0 code_pr -f pr_request.json --priority high
  
  # Deploy with idempotency
  $0 mrg_deploy -p '{"service": "api", "version": "v1.2.3"}' --idempotency deploy-v1.2.3

EOF
    exit 0
}

# Parse arguments
TASK_TYPE=""
PAYLOAD=""
PRIORITY="normal"
IDEMPOTENCY_KEY=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            ;;
        -p|--payload)
            PAYLOAD="$2"
            shift 2
            ;;
        -f|--file)
            if [[ ! -f "$2" ]]; then
                error "File not found: $2"
            fi
            PAYLOAD=$(cat "$2")
            shift 2
            ;;
        --priority)
            PRIORITY="$2"
            shift 2
            ;;
        --idempotency)
            IDEMPOTENCY_KEY="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --api-key)
            API_KEY="$2"
            shift 2
            ;;
        *)
            if [[ -z "$TASK_TYPE" ]]; then
                TASK_TYPE="$1"
            else
                error "Unknown option: $1"
            fi
            shift
            ;;
    esac
done

# Validate inputs
if [[ -z "$TASK_TYPE" ]]; then
    error "Task type is required"
fi

if [[ -z "$API_KEY" ]]; then
    error "API key is required (set AUREA_API_KEY or use --api-key)"
fi

# Set default payloads for common tasks
if [[ -z "$PAYLOAD" ]]; then
    case $TASK_TYPE in
        gen_content)
            PAYLOAD='{"prompt": "Generate test content", "model": "claude-3-sonnet-20240229"}'
            info "Using default payload for gen_content"
            ;;
        centerpoint)
            PAYLOAD='{"sync_type": "incremental", "entities": ["customers", "jobs"]}'
            info "Using default payload for centerpoint"
            ;;
        mrg_deploy)
            PAYLOAD='{"service": "api", "environment": "staging"}'
            info "Using default payload for mrg_deploy"
            ;;
        *)
            error "Payload is required for task type: $TASK_TYPE"
            ;;
    esac
fi

# Build request body
REQUEST_BODY=$(jq -n \
    --arg type "$TASK_TYPE" \
    --argjson payload "$PAYLOAD" \
    --arg priority "$PRIORITY" \
    '{type: $type, payload: $payload, priority: $priority}')

# Add idempotency key if provided
HEADERS=(-H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json")
if [[ -n "$IDEMPOTENCY_KEY" ]]; then
    HEADERS+=(-H "Idempotency-Key: $IDEMPOTENCY_KEY")
    REQUEST_BODY=$(echo "$REQUEST_BODY" | jq --arg key "$IDEMPOTENCY_KEY" '. + {idempotency_key: $key}')
fi

# Make API request
info "Enqueueing task: $TASK_TYPE"
info "API URL: $API_URL"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "${HEADERS[@]}" \
    -d "$REQUEST_BODY" \
    "$API_URL/tasks")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

# Check response
if [[ "$HTTP_CODE" == "200" ]] || [[ "$HTTP_CODE" == "201" ]]; then
    TASK_ID=$(echo "$BODY" | jq -r '.task_id')
    success "Task enqueued successfully!"
    echo "Task ID: $TASK_ID"
    echo "Status URL: $API_URL/tasks/$TASK_ID"
    echo ""
    echo "Full response:"
    echo "$BODY" | jq '.'
else
    error "Failed to enqueue task (HTTP $HTTP_CODE)"
    echo "Response: $BODY"
fi