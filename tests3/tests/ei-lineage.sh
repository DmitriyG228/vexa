#!/usr/bin/env bash
# ei-lineage (#24): meeting.completed → agent proposal branch → human-signed merge.
#
# Step IDs (stable — the proves[] of pack ei-lineage, issue #24):
#   hook_wired       — meeting-api's POST_MEETING_HOOKS points at agent-api's consumer
#   e2e_propose      — seeded workspace + synthetic completed meeting → proposal branch
#                      with schema-conforming meeting artifact + ≥1 entity update
#   idempotent       — duplicate meeting.completed deliveries → exactly one proposal
#   sign_gate        — main never changes without /sign; sign merges exactly the
#                      proposal (409 on re-sign); reject closes the branch with a note
#   crash_safe       — induced agent failure → no partial branch, visible failed
#                      status, retry produces one clean proposal
#   tenant_isolated  — two orgs land in two workspaces; cross-org reads are 404
#   flag_off_inert   — org flag off (default) → no claim, no run, no workspace
#
# The agent step is driven through the FULL real lineage (runtime-api org
# container, workspace clone transfer, exec, branch publication) with a
# deterministic agent command injected per-org via the admin-controlled
# user.data.ei.agent_cli seam — the same seam customer-local CLIs (Vibe/
# OpenCode, epic #21) use. No LLM credentials are required, so this stays a
# cheap-tier deterministic regression; the real-provider run is the P8 eyeball.
#
# Reads: .state/admin_url, .state/admin_token
# Writes: .state/reports/<mode>/ei-lineage.json
source "$(dirname "$0")/../lib/common.sh"

ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token 2>/dev/null || true)
if [ -z "$ADMIN_TOKEN" ]; then
    ADMIN_TOKEN=$(grep -E '^ADMIN_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 || true)
fi
ADMIN_TOKEN=${ADMIN_TOKEN:-changeme}

# agent-api binds 127.0.0.1:${AGENT_API_PORT:-8100} in compose.
if [ -z "${AGENT_API_URL:-}" ]; then
    AGENT_PORT=$(grep -E '^AGENT_API_PORT=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 || true)
    AGENT_API_URL="http://localhost:${AGENT_PORT:-8100}"
fi
# agent-api API_KEY = BOT_API_TOKEN (may be empty = auth disabled in dev).
EI_API_KEY=$(grep -E '^BOT_API_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 || true)

echo ""
echo "  ei-lineage"
echo "  ──────────────────────────────────────────────"

test_begin ei-lineage

EPOCH=$(date +%s)
ORGA="eitest-a-$EPOCH"
ORGB="eitest-b-$EPOCH"
MA1="9${EPOCH}01"; MA2="9${EPOCH}02"; MA3="9${EPOCH}03"
MB1="9${EPOCH}04"; MC1="9${EPOCH}05"

# Deterministic writer "agent": creates the meeting artifact from the template
# conventions + appends a dated confidence-scored entry inside the entity's
# routine-updates region. Runs inside the real org agent container.
WRITER='bash -ec "
F=graph/kg/entities/meetings/\${EI_MEETING_ID}-sync.md
mkdir -p graph/kg/entities/meetings graph/kg/entities/people
{
  echo \"# Meeting: Synthetic sync (\${EI_MEETING_ID})\"
  echo
  echo \"- id: \${EI_MEETING_ID}\"
  echo \"- platform: google_meet\"
  echo \"- participants: [[Jane Doe]]\"
  echo \"- companies: [[Acme Corp]]\"
  echo
  echo \"## Summary\"
  echo
  echo \"Synthetic meeting artifact written by the deterministic regression agent.\"
  echo
  echo \"## Decisions\"
  echo
  echo \"- [[Jane Doe]] to follow up (owner: [[Jane Doe]])\"
  echo
  echo \"## Action items\"
  echo
  echo \"- [ ] follow up — [[Jane Doe]], due n/a\"
} > \$F
sed -i \"s|<!-- routine-updates:end -->|- \$(date -u +%F) [conf:0.9] Discussed in meeting \${EI_MEETING_ID} (source: [[Meeting \${EI_MEETING_ID}]])\n<!-- routine-updates:end -->|\" graph/kg/entities/people/jane-doe.md
"'
CRASHER='bash -ec "exit 7"'

api_curl() {  # api_curl <curl args...> — adds X-API-Key when configured
    if [ -n "$EI_API_KEY" ]; then
        curl -s -H "X-API-Key: $EI_API_KEY" "$@"
    else
        curl -s "$@"
    fi
}

create_user() {  # create_user <email> → user id
    curl -s -X POST "$ADMIN_URL/admin/users" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
        -d "{\"email\":\"$1\",\"name\":\"EI Test\"}" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true
}

set_ei() {  # set_ei <user_id> <org_id> <agent_cli ('' = remove override)>
    python3 - "$1" "$2" "$3" <<'PY' > /tmp/.ei-patch.json
import json, sys
uid, org, cli = sys.argv[1], sys.argv[2], sys.argv[3]
ei = {"enabled": True, "org_id": org}
if cli:
    ei["agent_cli"] = cli
print(json.dumps({"data": {"ei": ei}}))
PY
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$ADMIN_URL/admin/users/$1" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
        -d @/tmp/.ei-patch.json)
    [ "$code" = "200" ]
}

deliver() {  # deliver <user_id> <meeting_id> [event_suffix] → response body
    curl -s -X POST "$AGENT_API_URL/internal/webhooks/meeting-completed" \
        -H "Content-Type: application/json" \
        -d "{\"event_id\":\"evt_eitest_$2${3:-}\",\"event_type\":\"meeting.completed\",\"api_version\":\"2026-03-01\",\"created_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"data\":{\"meeting\":{\"id\":$2,\"user_id\":$1,\"platform\":\"google_meet\",\"status\":\"completed\",\"title\":\"EI synthetic meeting $2\"}}}"
}

run_status() {  # run_status <org> <meeting> → claim status string ('' if none)
    curl -s "$AGENT_API_URL/internal/ei/status?org_id=$1&meeting_id=$2" \
        | python3 -c "import sys,json; c=json.load(sys.stdin).get('claim'); print(c.get('status','') if c else '')" 2>/dev/null || true
}

wait_status() {  # wait_status <org> <meeting> <want> <timeout_s>
    local i=0 st=""
    while [ $i -lt "$4" ]; do
        st=$(run_status "$1" "$2")
        [ "$st" = "$3" ] && return 0
        # fail fast when waiting for success but the run already failed
        if [ "$3" = "proposed" ] && [ "$st" = "failed" ]; then return 1; fi
        sleep 3; i=$((i+3))
    done
    return 1
}

ws_git() {  # ws_git <org> <git args...>
    local org="$1"; shift
    svc_exec agent-api git -C "/workspaces/$org/repo" "$@"
}

proposals_for() {  # proposals_for <org> <meeting_id> → count
    api_curl "$AGENT_API_URL/api/proposals?org=$1" | python3 -c "
import sys,json
mid=int('$2')
print(sum(1 for p in json.load(sys.stdin) if p.get('meeting_id')==mid))" 2>/dev/null || echo -1
}

proposal_id_for() {  # proposal_id_for <org> <meeting_id>
    api_curl "$AGENT_API_URL/api/proposals?org=$1" | python3 -c "
import sys,json
mid=int('$2')
for p in json.load(sys.stdin):
    if p.get('meeting_id')==mid: print(p['id']); break" 2>/dev/null || true
}

# ── Setup (not steps): agent-api up + three users ─────────────────
if ! curl -sf "$AGENT_API_URL/health" > /dev/null 2>&1; then
    step_fail hook_wired "agent-api not reachable at $AGENT_API_URL"
    exit 1
fi
UA=$(create_user "ei-a-$EPOCH@test.vexa.ai")
UB=$(create_user "ei-b-$EPOCH@test.vexa.ai")
UC=$(create_user "ei-c-$EPOCH@test.vexa.ai")
if [ -z "$UA" ] || [ -z "$UB" ] || [ -z "$UC" ]; then
    step_fail hook_wired "could not create test users via $ADMIN_URL"
    exit 1
fi
set_ei "$UA" "$ORGA" "$WRITER" || { step_fail hook_wired "PATCH ei config failed for user $UA"; exit 1; }
set_ei "$UB" "$ORGB" "$WRITER" || { step_fail hook_wired "PATCH ei config failed for user $UB"; exit 1; }
info "users: A=$UA ($ORGA) B=$UB ($ORGB) C=$UC (flag off)"

# ── Step: hook_wired ───────────────────────────────────────────────
HOOKS=$(svc_exec meeting-api sh -c 'echo $POST_MEETING_HOOKS' 2>/dev/null || echo "")
if echo "$HOOKS" | grep -q "agent-api.*meeting-completed"; then
    step_pass hook_wired "POST_MEETING_HOOKS → agent-api consumer ($HOOKS)"
else
    step_fail hook_wired "POST_MEETING_HOOKS does not target agent-api: '$HOOKS'"
fi

# ── Step: e2e_propose ─────────────────────────────────────────────
R=$(deliver "$UA" "$MA1")
ST=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
if [ "$ST" != "accepted" ]; then
    step_fail e2e_propose "delivery not accepted: $R"
    exit 1
fi
if ! wait_status "$ORGA" "$MA1" proposed 180; then
    step_fail e2e_propose "run did not reach 'proposed' (last status: $(run_status "$ORGA" "$MA1"))"
    exit 1
fi
PID1=$(proposal_id_for "$ORGA" "$MA1")
BR_OK=$(ws_git "$ORGA" rev-parse --verify "refs/heads/meeting/$MA1" 2>/dev/null || echo "")
DIFF=$(api_curl "$AGENT_API_URL/api/proposals/$PID1/diff?org=$ORGA")
E2E_VERDICT=$(echo "$DIFF" | python3 -c "
import sys, json
d = json.load(sys.stdin)
files = d.get('files', [])
meeting = [f for f in files if f['path'].startswith('graph/kg/entities/meetings/') and f['status'] == 'added']
entity = [f for f in files if f['path'] == 'graph/kg/entities/people/jane-doe.md' and f['status'] == 'modified']
schema = meeting and all(s in meeting[0]['after'] for s in ('## Summary', '## Decisions', '## Action items', '[[Jane Doe]]'))
appended = entity and '[conf:0.9] Discussed in meeting $MA1' in entity[0]['after']
print('ok' if (meeting and entity and schema and appended) else f'bad: meeting={len(meeting)} entity={len(entity)} schema={bool(schema)} appended={bool(appended)}')" 2>/dev/null || echo parse-error)
if [ -n "$PID1" ] && [ -n "$BR_OK" ] && [ "$E2E_VERDICT" = "ok" ]; then
    step_pass e2e_propose "proposal $PID1 on meeting/$MA1: conforming artifact + entity update"
else
    step_fail e2e_propose "pid=$PID1 branch=$BR_OK diff=$E2E_VERDICT"
fi

# ── Step: idempotent ──────────────────────────────────────────────
R2=$(deliver "$UA" "$MA1" "-dup")
ST2=$(echo "$R2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
N1=$(proposals_for "$ORGA" "$MA1")
if [ "$ST2" = "duplicate" ] && [ "$N1" = "1" ]; then
    step_pass idempotent "duplicate delivery → '$ST2', still exactly 1 proposal"
else
    step_fail idempotent "redelivery status='$ST2', proposals for $MA1: $N1"
fi

# ── Step: sign_gate ───────────────────────────────────────────────
SIGN_OK=1
MAIN0=$(ws_git "$ORGA" rev-parse main)
SEED0=$(ws_git "$ORGA" rev-list --max-parents=0 main | head -1)
if [ "$MAIN0" != "$SEED0" ]; then
    SIGN_OK=0; info "main moved without sign: $MAIN0 != seed $SEED0"
fi
SIGN_RESP=$(api_curl -X POST "$AGENT_API_URL/api/proposals/$PID1/sign?org=$ORGA")
MERGED=$(echo "$SIGN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d.get('merged') and d.get('merge_commit') else 'no')" 2>/dev/null || echo no)
MAIN1=$(ws_git "$ORGA" rev-parse main)
MC=$(echo "$SIGN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('merge_commit',''))" 2>/dev/null || true)
ARTIFACT_ON_MAIN=$(ws_git "$ORGA" ls-tree -r --name-only main | grep -c "graph/kg/entities/meetings/$MA1" || true)
[ "$MERGED" = "yes" ] && [ "$MAIN1" = "$MC" ] && [ "$ARTIFACT_ON_MAIN" -ge 1 ] || { SIGN_OK=0; info "sign: merged=$MERGED main=$MAIN1 mc=$MC artifact=$ARTIFACT_ON_MAIN resp=$SIGN_RESP"; }
RESIGN_CODE=$(api_curl -o /dev/null -w '%{http_code}' -X POST "$AGENT_API_URL/api/proposals/$PID1/sign?org=$ORGA")
[ "$RESIGN_CODE" = "409" ] || { SIGN_OK=0; info "re-sign returned $RESIGN_CODE (want 409)"; }

# reject path on a second proposal
deliver "$UA" "$MA2" > /dev/null
if wait_status "$ORGA" "$MA2" proposed 180; then
    PID2=$(proposal_id_for "$ORGA" "$MA2")
    MAIN2=$(ws_git "$ORGA" rev-parse main)
    NONOTE_CODE=$(api_curl -o /dev/null -w '%{http_code}' -X POST "$AGENT_API_URL/api/proposals/$PID2/reject?org=$ORGA" -H "Content-Type: application/json" -d '{}')
    case "$NONOTE_CODE" in 400|422) : ;; *) SIGN_OK=0; info "reject without note returned $NONOTE_CODE (want 400/422)";; esac
    REJ=$(api_curl -X POST "$AGENT_API_URL/api/proposals/$PID2/reject?org=$ORGA" -H "Content-Type: application/json" -d '{"note":"synthetic regression reject"}')
    CLOSED=$(echo "$REJ" | python3 -c "import sys,json; print('yes' if json.load(sys.stdin).get('closed') else 'no')" 2>/dev/null || echo no)
    BR2=$(ws_git "$ORGA" rev-parse --verify "refs/heads/meeting/$MA2" 2>/dev/null || echo "")
    MAIN3=$(ws_git "$ORGA" rev-parse main)
    SIGN_AFTER_REJECT=$(api_curl -o /dev/null -w '%{http_code}' -X POST "$AGENT_API_URL/api/proposals/$PID2/sign?org=$ORGA")
    if [ "$CLOSED" != "yes" ] || [ -n "$BR2" ] || [ "$MAIN3" != "$MAIN2" ] || [ "$SIGN_AFTER_REJECT" != "409" ]; then
        SIGN_OK=0; info "reject: closed=$CLOSED branch='$BR2' main $MAIN2→$MAIN3 sign-after=$SIGN_AFTER_REJECT"
    fi
else
    SIGN_OK=0; info "second proposal ($MA2) never reached proposed"
fi
if [ "$SIGN_OK" = "1" ]; then
    step_pass sign_gate "main moves only via /sign; re-sign 409; reject closes branch with note"
else
    step_fail sign_gate "see info lines above"
fi

# ── Step: crash_safe ──────────────────────────────────────────────
CRASH_OK=1
set_ei "$UA" "$ORGA" "$CRASHER" || CRASH_OK=0
deliver "$UA" "$MA3" > /dev/null
if wait_status "$ORGA" "$MA3" failed 120; then
    BR3=$(ws_git "$ORGA" rev-parse --verify "refs/heads/meeting/$MA3" 2>/dev/null || echo "")
    [ -z "$BR3" ] || { CRASH_OK=0; info "partial branch meeting/$MA3 exists after crash"; }
else
    CRASH_OK=0; info "crash run never reached visible 'failed' status"
fi
# retry after fixing the agent → exactly one clean proposal
set_ei "$UA" "$ORGA" "$WRITER" || CRASH_OK=0
R3=$(deliver "$UA" "$MA3" "-retry")
ST3=$(echo "$R3" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
[ "$ST3" = "accepted" ] || { CRASH_OK=0; info "retry delivery not re-claimed: $R3"; }
if wait_status "$ORGA" "$MA3" proposed 180; then
    N3=$(proposals_for "$ORGA" "$MA3")
    BR3=$(ws_git "$ORGA" rev-parse --verify "refs/heads/meeting/$MA3" 2>/dev/null || echo "")
    [ "$N3" = "1" ] && [ -n "$BR3" ] || { CRASH_OK=0; info "retry: proposals=$N3 branch='$BR3'"; }
else
    CRASH_OK=0; info "retry run never proposed (status: $(run_status "$ORGA" "$MA3"))"
fi
if [ "$CRASH_OK" = "1" ]; then
    step_pass crash_safe "crash → failed status, no branch; retry → one clean proposal"
else
    step_fail crash_safe "see info lines above"
fi

# ── Step: tenant_isolated ─────────────────────────────────────────
ISO_OK=1
deliver "$UB" "$MB1" > /dev/null
if wait_status "$ORGB" "$MB1" proposed 180; then
    PIDB=$(proposal_id_for "$ORGB" "$MB1")
    NB_IN_A=$(proposals_for "$ORGA" "$MB1")
    CROSS_CODE=$(api_curl -o /dev/null -w '%{http_code}' "$AGENT_API_URL/api/proposals/$PIDB/diff?org=$ORGA")
    BR_B_IN_A=$(ws_git "$ORGA" rev-parse --verify "refs/heads/meeting/$MB1" 2>/dev/null || echo "")
    WS_B=$(svc_exec agent-api test -d "/workspaces/$ORGB/repo/.git" && echo yes || echo no)
    if [ "$NB_IN_A" != "0" ] || [ "$CROSS_CODE" != "404" ] || [ -n "$BR_B_IN_A" ] || [ "$WS_B" != "yes" ]; then
        ISO_OK=0; info "isolation: B-in-A=$NB_IN_A cross-org-diff=$CROSS_CODE branch-in-A='$BR_B_IN_A' wsB=$WS_B"
    fi
else
    ISO_OK=0; info "org B run never proposed (status: $(run_status "$ORGB" "$MB1"))"
fi
if [ "$ISO_OK" = "1" ]; then
    step_pass tenant_isolated "two orgs → two workspaces; cross-org reads are 404"
else
    step_fail tenant_isolated "see info lines above"
fi

# ── Step: flag_off_inert ──────────────────────────────────────────
RC=$(deliver "$UC" "$MC1")
STC=$(echo "$RC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
sleep 2
CLAIM_C=$(run_status "user-$UC" "$MC1")
WS_C=$(svc_exec agent-api test -d "/workspaces/user-$UC" && echo yes || echo no)
if [ "$STC" = "inert" ] && [ -z "$CLAIM_C" ] && [ "$WS_C" = "no" ]; then
    step_pass flag_off_inert "flag off (default) → '$STC', no claim, no workspace"
else
    step_fail flag_off_inert "status='$STC' claim='$CLAIM_C' workspace=$WS_C"
fi

test_end
