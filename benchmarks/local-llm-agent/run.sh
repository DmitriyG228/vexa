#!/usr/bin/env bash
# Single entrypoint for the local-llm-agent benchmark (pack ei-agent, issue #22).
#
#   ARM=<1-4> MODEL=<id> ./run.sh          # run one arm over T1+T2+T3
#   ARM=2 MODEL=mistral-small-latest TASKS=t1 ./run.sh
#
# Arms:
#   1  Claude Code + Claude            (host credentials: ANTHROPIC_API_KEY or host `claude` login)
#   2  Claude Code + Mistral           (via local LiteLLM /v1/messages — env-only rewiring)
#   3  Mistral Vibe + Mistral direct
#   4  OpenCode + Mistral direct       (OpenAI-compatible)
#
# Secrets (HARD RULE): the Mistral key is injected via the environment, never on disk:
#   export MISTRAL_API_KEY="$(cd <vexa-secrets-checkout> && bin/secret no-prod/mistral.enc.env MISTRAL_API_KEY)"
set -euo pipefail
cd "$(dirname "$0")"

ARM="${ARM:?set ARM=1|2|3|4}"
case "$ARM" in
  1) MODEL="${MODEL:-claude-opus-4-8}" ;;
  *) MODEL="${MODEL:-mistral-small-latest}" ;;
esac
TASKS="${TASKS:-t1,t2,t3}"

echo "═══ local-llm-agent benchmark ═══ arm=$ARM model=$MODEL tasks=$TASKS"

if [ "$ARM" != "1" ] && [ -z "${MISTRAL_API_KEY:-}" ]; then
  echo "ERROR: MISTRAL_API_KEY missing. Inject via the sanctioned seam:" >&2
  echo '  export MISTRAL_API_KEY="$(cd <vexa-secrets> && bin/secret no-prod/mistral.enc.env MISTRAL_API_KEY)"' >&2
  exit 2
fi

# ── agent images (built once, cached) ─────────────────────────────────────────
case "$ARM" in
  1|2) docker build -q -t vexa-bench-claude   -f docker/Dockerfile.claude-code docker >/dev/null ;;
  3)   docker build -q -t vexa-bench-vibe     -f docker/Dockerfile.vibe       docker >/dev/null ;;
  4)   docker build -q -t vexa-bench-opencode -f docker/Dockerfile.opencode   docker >/dev/null ;;
esac

# ── arm 2: LiteLLM proxy exposing the Anthropic /v1/messages route ───────────
if [ "$ARM" = "2" ]; then
  # Local-only shared secret between agent container and proxy; fresh per invocation,
  # passed via env only (never written to disk).
  export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-bench-$(openssl rand -hex 12)}"
  docker compose -f compose.yaml up -d --force-recreate --wait litellm
  # config faithfulness: prove the /v1/messages route answers BEFORE burning agent runs
  curl -sf http://127.0.0.1:4400/v1/messages \
    -H "x-api-key: $LITELLM_MASTER_KEY" -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}" \
    >/dev/null || { echo "ERROR: LiteLLM /v1/messages preflight failed" >&2; exit 1; }
  echo "  litellm /v1/messages preflight: OK"
fi

python3 lib/runner.py --arm "$ARM" --model "$MODEL" --tasks "$TASKS"
RC=$?

if [ "$ARM" = "2" ] && [ "${KEEP_LITELLM:-0}" != "1" ]; then
  docker compose -f compose.yaml down >/dev/null 2>&1 || true
fi
exit $RC
