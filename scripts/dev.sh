#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend/atlas-testops-web"
STATE_DIR="$ROOT_DIR/tmp/dev"
LOG_DIR="$ROOT_DIR/logs/dev"

BACKEND_PID_FILE="$STATE_DIR/backend.pid"
FRONTEND_PID_FILE="$STATE_DIR/frontend.pid"
BACKEND_ENDPOINT_FILE="$STATE_DIR/backend.endpoint"
FRONTEND_ENDPOINT_FILE="$STATE_DIR/frontend.endpoint"
BACKEND_LOG_FILE="$LOG_DIR/backend.log"
FRONTEND_LOG_FILE="$LOG_DIR/frontend.log"

API_HOST="${ATLAS_DEV_API_HOST:-127.0.0.1}"
API_PORT="${ATLAS_DEV_API_PORT:-8001}"
WEB_HOST="${ATLAS_DEV_WEB_HOST:-127.0.0.1}"
WEB_PORT="${ATLAS_DEV_WEB_PORT:-5173}"
OWNER_DATABASE_URL="${ATLAS_DEV_OWNER_DATABASE_URL:-postgresql://atlas_owner:atlas_owner@127.0.0.1:5432/atlas}"
INSTALL_DEPENDENCIES="${ATLAS_DEV_INSTALL_DEPENDENCIES:-true}"
SEED_EXAMPLES="${ATLAS_DEV_SEED_EXAMPLES:-true}"

STARTED_BACKEND_THIS_RUN=false
STARTED_FRONTEND_THIS_RUN=false

info() {
  printf '\033[1;34m[atlas]\033[0m %s\n' "$*"
}

success() {
  printf '\033[1;32m[atlas]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[atlas]\033[0m %s\n' "$*" >&2
}

fail() {
  printf '\033[1;31m[atlas]\033[0m %s\n' "$*" >&2
  return 1
}

usage() {
  cat <<'EOF'
Atlas TestOps 本地开发环境

用法：
  ./scripts/dev.sh [start|stop|restart|seed|status|logs|help]

命令：
  start                 一键启动全部本地服务（默认）
  stop                  停止本脚本管理的前后端及默认 Compose 服务
  restart               重启全部本地服务
  seed                  向当前配置的 Tenant / Project 补齐示例资产和用例
  status                查看本地服务与 Compose 服务状态
  logs [backend]        跟随后端日志
  logs frontend         跟随前端日志
  logs infra            跟随基础设施与默认 Worker 日志
  help                  显示帮助

可选环境变量：
  ATLAS_DEV_API_HOST                 后端监听地址，默认 127.0.0.1
  ATLAS_DEV_API_PORT                 后端端口，默认 8001
  ATLAS_DEV_WEB_HOST                 前端监听地址，默认 127.0.0.1
  ATLAS_DEV_WEB_PORT                 前端端口，默认 5173
  ATLAS_DEV_OWNER_DATABASE_URL       Alembic 使用的 Owner DSN
  ATLAS_DEV_INSTALL_DEPENDENCIES     是否同步锁定依赖，默认 true
  ATLAS_DEV_SEED_EXAMPLES            启动时是否初始化示例数据，默认 true
  ATLAS_DEV_TENANT_ID                示例数据目标 Tenant，默认读取前端 .env.local
  ATLAS_DEV_PROJECT_ID               示例数据目标 Project，默认读取前端 .env.local
EOF
}

is_true() {
  local normalized
  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"

  case "$normalized" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

require_command() {
  local command_name="$1"
  local install_hint="$2"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "缺少命令：${command_name}。$install_hint"
  fi
}

prepare_directories() {
  mkdir -p "$STATE_DIR" "$LOG_DIR"
}

copy_env_if_missing() {
  local example_file="$1"
  local target_file="$2"

  if [[ -f "$target_file" ]]; then
    return
  fi

  cp "$example_file" "$target_file"
  info "已根据 $(basename "$example_file") 创建 ${target_file#"$ROOT_DIR"/}"
}

read_env_value() {
  local key="$1"
  local env_file="$2"

  if [[ ! -f "$env_file" ]]; then
    return 1
  fi

  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/\r$/, "")
      print
      exit
    }
  ' "$env_file"
}

read_pid() {
  local pid_file="$1"

  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi

  local pid
  pid="$(tr -d '[:space:]' <"$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    return 1
  fi

  printf '%s\n' "$pid"
}

is_process_running() {
  local pid_file="$1"
  local pid

  pid="$(read_pid "$pid_file" 2>/dev/null)" || return 1
  kill -0 "$pid" 2>/dev/null
}

listener_description() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed -n '2p' || true
}

ensure_endpoint_available() {
  local label="$1"
  local host="$2"
  local port="$3"
  local pid_file="$4"
  local endpoint_file="$5"
  local listener
  local requested_endpoint="$host:$port"
  local running_endpoint

  if is_process_running "$pid_file"; then
    if [[ ! -f "$endpoint_file" ]]; then
      fail "$label 已在运行，但缺少端口状态。请先执行 ./scripts/dev.sh stop。"
    fi

    running_endpoint="$(tr -d '[:space:]' <"$endpoint_file")"
    if [[ "$running_endpoint" != "$requested_endpoint" ]]; then
      fail "$label 已运行在 ${running_endpoint}，当前请求为 ${requested_endpoint}。请先停止后再切换端口。"
    fi
    return
  fi

  listener="$(listener_description "$port")"
  if [[ -n "$listener" ]]; then
    fail "$label 端口 $host:$port 已被占用：$listener"
  fi
}

preflight() {
  require_command docker "请安装并启动 Docker Desktop。"
  require_command uv "请安装 uv >= 0.11。"
  require_command node "请安装 Node.js >= 22.13.0。"
  require_command pnpm "请安装 pnpm。"
  require_command curl "请安装 curl。"
  require_command lsof "请安装 lsof。"
  require_command pgrep "请安装 pgrep。"

  if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon 不可用，请先启动 Docker Desktop。"
  fi

  if [[ "$API_PORT" == "$WEB_PORT" ]]; then
    fail "后端和前端不能使用同一个端口：$API_PORT"
  fi

  ensure_endpoint_available \
    "后端" \
    "$API_HOST" \
    "$API_PORT" \
    "$BACKEND_PID_FILE" \
    "$BACKEND_ENDPOINT_FILE"
  ensure_endpoint_available \
    "前端" \
    "$WEB_HOST" \
    "$WEB_PORT" \
    "$FRONTEND_PID_FILE" \
    "$FRONTEND_ENDPOINT_FILE"
}

prepare_environment() {
  prepare_directories
  copy_env_if_missing "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
  copy_env_if_missing "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.local"
}

install_dependencies() {
  if ! is_true "$INSTALL_DEPENDENCIES"; then
    info "已跳过依赖同步。"
    return
  fi

  info "同步后端锁定依赖..."
  (
    cd "$BACKEND_DIR"
    uv sync --locked
  )

  info "同步前端锁定依赖..."
  (
    cd "$FRONTEND_DIR"
    CI=true pnpm install --frozen-lockfile
  )
}

start_infrastructure() {
  info "启动 PostgreSQL、Temporal 和 MinIO..."
  (
    cd "$ROOT_DIR"
    docker compose up -d --wait postgres temporal minio
  )

  info "执行数据库 migration..."
  (
    cd "$BACKEND_DIR"
    ATLAS_DATABASE_URL="$OWNER_DATABASE_URL" uv run alembic upgrade head
  )

  info "构建并启动 Auth Session、Fixture、Debug Preparation 与 Browser Worker..."
  (
    cd "$ROOT_DIR"
    ATLAS_DEV_API_PORT="$API_PORT" docker compose up -d --build --wait \
      auth-session-worker \
      fixture-worker \
      temporal-worker \
      browser-worker
  )
}

start_backend() {
  if is_process_running "$BACKEND_PID_FILE"; then
    info "后端已在运行（PID $(read_pid "$BACKEND_PID_FILE")）。"
    return
  fi

  rm -f "$BACKEND_PID_FILE" "$BACKEND_ENDPOINT_FILE"
  {
    printf '\n===== %s backend start =====\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  } >>"$BACKEND_LOG_FILE"

  (
    cd "$BACKEND_DIR"
    exec nohup env \
      ATLAS_CORS_ORIGINS="[\"http://localhost:$WEB_PORT\",\"http://127.0.0.1:$WEB_PORT\"]" \
      ATLAS_AUTH_SESSION_DISPATCH_ENABLED=true \
      ATLAS_FIXTURE_DISPATCH_ENABLED=true \
      ATLAS_BROWSER_RUNTIME_ENABLED=true \
      ATLAS_DEBUG_RUN_PREPARATION_ENABLED=true \
      ATLAS_DEBUG_RUN_PREPARATION_ACTIVITY_TIMEOUT_SECONDS=360 \
      ATLAS_BROWSER_RUNTIME_WORKER_IDENTITY=browser-worker-local \
      ATLAS_BROWSER_RUNTIME_ACTIVITY_TIMEOUT_SECONDS=300 \
      ATLAS_BROWSER_RUNTIME_HEARTBEAT_TIMEOUT_SECONDS=20 \
      ATLAS_BROWSER_RUNTIME_PERMIT_TTL_SECONDS=420 \
      ATLAS_BROWSER_RUNTIME_PERMIT_KEY_BASE64=YXRsYXMtbG9jYWwtYnJvd3Nlci1wZXJtaXQta2V5ISE= \
      ATLAS_BROWSER_RUNTIME_REQUEST_HMAC_KEY_BASE64=YXRsYXMtbG9jYWwtYnJvd3Nlci1yZXF1ZXN0LWtleSE= \
      ATLAS_BROWSER_CONTEXT_ENVELOPE_KEY_BASE64=YXRsYXMtbG9jYWwtY29udGV4dC1rZXktMzJieXRlcyE= \
      ATLAS_BROWSER_CONTEXT_ENVELOPE_KEY_VERSION=local-v1 \
      ATLAS_BROWSER_REVISION=playwright@1.61.0/chromium@149.0.7827.0 \
      ATLAS_BROWSER_TOOL_CATALOG_REF=tools.local-public-web@1.0.0 \
      ATLAS_BROWSER_POLICY_BUNDLE_REF=policy.local-public-web@1.0.0 \
      ATLAS_BROWSER_ALLOWED_ACTIONS='["open_route","activate","enter_text","keypress","capture_view"]' \
      ATLAS_EVIDENCE_OBJECT_STORE_ENDPOINT=127.0.0.1:9000 \
      ATLAS_EVIDENCE_OBJECT_STORE_ACCESS_KEY=atlas_minio \
      ATLAS_EVIDENCE_OBJECT_STORE_SECRET_KEY=atlas_minio_password \
      ATLAS_EVIDENCE_OBJECT_STORE_BUCKET=atlas-evidence-artifacts \
      uv run uvicorn atlas_testops.main:app \
      --host "$API_HOST" \
      --port "$API_PORT" \
      --reload
  ) >>"$BACKEND_LOG_FILE" 2>&1 </dev/null &

  printf '%s\n' "$!" >"$BACKEND_PID_FILE"
  printf '%s:%s\n' "$API_HOST" "$API_PORT" >"$BACKEND_ENDPOINT_FILE"
  STARTED_BACKEND_THIS_RUN=true
  info "后端进程已启动（PID $(read_pid "$BACKEND_PID_FILE")）。"
}

start_frontend() {
  if is_process_running "$FRONTEND_PID_FILE"; then
    info "前端已在运行（PID $(read_pid "$FRONTEND_PID_FILE")）。"
    return
  fi

  rm -f "$FRONTEND_PID_FILE" "$FRONTEND_ENDPOINT_FILE"
  {
    printf '\n===== %s frontend start =====\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  } >>"$FRONTEND_LOG_FILE"

  (
    cd "$FRONTEND_DIR"
    # Limit the process environment before exposing it to the local Worker.
    exec nohup env -i \
      HOME="$HOME" \
      PATH="$PATH" \
      TMPDIR="${TMPDIR:-/tmp}" \
      USER="${USER:-atlas}" \
      LANG="${LANG:-C.UTF-8}" \
      SHELL="${SHELL:-/bin/sh}" \
      ATLAS_API_ORIGIN="http://$API_HOST:$API_PORT" \
      CLOUDFLARE_INCLUDE_PROCESS_ENV=true \
      pnpm dev \
      --host "$WEB_HOST" \
      --port "$WEB_PORT"
  ) >>"$FRONTEND_LOG_FILE" 2>&1 </dev/null &

  printf '%s\n' "$!" >"$FRONTEND_PID_FILE"
  printf '%s:%s\n' "$WEB_HOST" "$WEB_PORT" >"$FRONTEND_ENDPOINT_FILE"
  STARTED_FRONTEND_THIS_RUN=true
  info "前端进程已启动（PID $(read_pid "$FRONTEND_PID_FILE")）。"
}

wait_for_http() {
  local label="$1"
  local url="$2"
  local pid_file="$3"
  local log_file="$4"
  local maximum_attempts="${5:-90}"
  local attempt=1

  info "等待 $label 就绪：$url"
  while ((attempt <= maximum_attempts)); do
    if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null 2>&1; then
      success "$label 已就绪。"
      return
    fi

    if ! is_process_running "$pid_file"; then
      warn "$label 进程已退出，最近日志如下："
      tail -n 40 "$log_file" >&2 || true
      return 1
    fi

    sleep 1
    ((attempt += 1))
  done

  warn "$label 在 ${maximum_attempts}s 内未就绪，最近日志如下："
  tail -n 40 "$log_file" >&2 || true
  return 1
}

verify_workers() {
  local running_services
  local worker

  running_services="$(
    cd "$ROOT_DIR"
    docker compose ps --status running --services
  )"

  for worker in \
    auth-session-worker \
    fixture-worker \
    temporal-worker \
    browser-worker; do
    if ! grep -qx "$worker" <<<"$running_services"; then
      warn "$worker 未保持运行，最近日志如下："
      (
        cd "$ROOT_DIR"
        docker compose logs --tail=40 "$worker"
      ) >&2 || true
      return 1
    fi
  done
}

seed_examples() {
  local tenant_id="${ATLAS_DEV_TENANT_ID:-}"
  local project_id="${ATLAS_DEV_PROJECT_ID:-}"

  if ! is_true "$SEED_EXAMPLES"; then
    info "已跳过本地示例数据初始化。"
    return
  fi

  if [[ -z "$tenant_id" ]]; then
    tenant_id="$(
      read_env_value \
        "NEXT_PUBLIC_ATLAS_TENANT_ID" \
        "$FRONTEND_DIR/.env.local"
    )"
  fi
  if [[ -z "$project_id" ]]; then
    project_id="$(
      read_env_value \
        "NEXT_PUBLIC_ATLAS_PROJECT_ID" \
        "$FRONTEND_DIR/.env.local"
    )"
  fi

  if [[ -z "$tenant_id" || -z "$project_id" ]]; then
    warn "未配置 Tenant / Project，已跳过示例数据。可设置 ATLAS_DEV_TENANT_ID 与 ATLAS_DEV_PROJECT_ID 后执行 ./scripts/dev.sh seed。"
    return
  fi

  info "初始化公共网页搜索资产与示例用例..."
  if ! (
    cd "$BACKEND_DIR"
    uv run python scripts/seed_local_examples.py \
      --api-origin "http://$API_HOST:$API_PORT" \
      --tenant-id "$tenant_id" \
      --project-id "$project_id"
  ); then
    return 1
  fi
  success "本地示例数据已就绪。"
}

signal_process_tree() {
  local signal_name="$1"
  local pid="$2"
  local child

  while read -r child; do
    if [[ -n "$child" ]]; then
      signal_process_tree "$signal_name" "$child"
    fi
  done < <(pgrep -P "$pid" 2>/dev/null || true)

  kill "-$signal_name" "$pid" 2>/dev/null || true
}

stop_managed_process() {
  local label="$1"
  local pid_file="$2"
  local endpoint_file="$3"
  local pid
  local attempt

  pid="$(read_pid "$pid_file" 2>/dev/null)" || {
    rm -f "$pid_file" "$endpoint_file"
    info "$label 未由启动脚本管理。"
    return
  }

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pid_file" "$endpoint_file"
    info "$label 已停止。"
    return
  fi

  info "停止 ${label}（PID ${pid}）..."
  signal_process_tree TERM "$pid"
  for attempt in $(seq 1 50); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file" "$endpoint_file"
      success "$label 已停止。"
      return
    fi
    sleep 0.2
  done

  warn "$label 未在 10 秒内退出，发送 KILL。"
  signal_process_tree KILL "$pid"
  rm -f "$pid_file" "$endpoint_file"
}

rollback_local_processes() {
  if [[ "$STARTED_FRONTEND_THIS_RUN" == true ]]; then
    stop_managed_process "前端" "$FRONTEND_PID_FILE" "$FRONTEND_ENDPOINT_FILE"
  fi
  if [[ "$STARTED_BACKEND_THIS_RUN" == true ]]; then
    stop_managed_process "后端" "$BACKEND_PID_FILE" "$BACKEND_ENDPOINT_FILE"
  fi
}

print_endpoints() {
  cat <<EOF

本地入口：
  前端          http://$WEB_HOST:$WEB_PORT
  后端 API      http://$API_HOST:$API_PORT
  API 文档      http://$API_HOST:$API_PORT/docs
  Temporal UI   http://127.0.0.1:8233
  MinIO Console http://127.0.0.1:9001

常用命令：
  ./scripts/dev.sh status
  ./scripts/dev.sh seed
  ./scripts/dev.sh logs backend
  ./scripts/dev.sh logs frontend
  ./scripts/dev.sh stop
EOF
}

start_all() {
  prepare_directories
  preflight
  prepare_environment
  install_dependencies
  start_infrastructure
  start_backend

  if ! wait_for_http \
    "后端" \
    "http://$API_HOST:$API_PORT/v1/health/ready" \
    "$BACKEND_PID_FILE" \
    "$BACKEND_LOG_FILE" \
    90; then
    rollback_local_processes
    fail "后端启动失败。"
  fi

  if ! seed_examples; then
    rollback_local_processes
    fail "本地示例数据初始化失败。"
  fi

  start_frontend
  if ! wait_for_http \
    "前端" \
    "http://$WEB_HOST:$WEB_PORT/login" \
    "$FRONTEND_PID_FILE" \
    "$FRONTEND_LOG_FILE" \
    120; then
    rollback_local_processes
    fail "前端启动失败。"
  fi

  if ! wait_for_http \
    "前端 BFF" \
    "http://$WEB_HOST:$WEB_PORT/api/atlas/v1/health/ready" \
    "$FRONTEND_PID_FILE" \
    "$FRONTEND_LOG_FILE" \
    30; then
    rollback_local_processes
    fail "前端 BFF 无法连接后端。"
  fi

  if ! verify_workers; then
    rollback_local_processes
    fail "默认 Worker 启动失败。"
  fi

  success "Atlas TestOps 本地开发环境已全部启动。"
  print_endpoints
}

seed_only() {
  prepare_directories
  prepare_environment
  require_command uv "请安装 uv >= 0.11。"
  require_command curl "请安装 curl。"

  if ! curl \
    --silent \
    --show-error \
    --fail \
    --max-time 3 \
    "http://$API_HOST:$API_PORT/v1/health/ready" >/dev/null; then
    fail "后端尚未就绪，请先执行 ./scripts/dev.sh。"
  fi

  seed_examples
}

stop_all() {
  prepare_directories
  stop_managed_process "前端" "$FRONTEND_PID_FILE" "$FRONTEND_ENDPOINT_FILE"
  stop_managed_process "后端" "$BACKEND_PID_FILE" "$BACKEND_ENDPOINT_FILE"

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    info "停止默认 Compose 服务..."
    (
      cd "$ROOT_DIR"
      docker compose stop \
        browser-worker \
        temporal-worker \
        fixture-worker \
        auth-session-worker \
        minio \
        temporal \
        postgres
    )
  else
    warn "Docker daemon 不可用，已跳过 Compose 服务停止。"
  fi

  success "Atlas TestOps 本地开发环境已停止。"
}

print_process_status() {
  local label="$1"
  local pid_file="$2"
  local endpoint_file="$3"
  local endpoint="unknown endpoint"

  if is_process_running "$pid_file"; then
    if [[ -f "$endpoint_file" ]]; then
      endpoint="$(tr -d '[:space:]' <"$endpoint_file")"
    fi
    printf '  %-18s RUNNING (PID %s, %s)\n' \
      "$label" \
      "$(read_pid "$pid_file")" \
      "$endpoint"
  else
    printf '  %-18s STOPPED\n' "$label"
  fi
}

show_status() {
  prepare_directories
  printf '本地进程：\n'
  print_process_status "Backend" "$BACKEND_PID_FILE" "$BACKEND_ENDPOINT_FILE"
  print_process_status "Frontend" "$FRONTEND_PID_FILE" "$FRONTEND_ENDPOINT_FILE"
  printf '\nCompose 服务：\n'

  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    (
      cd "$ROOT_DIR"
      docker compose ps --all
    )
  else
    warn "Docker daemon 不可用。"
  fi
}

follow_logs() {
  local target="${1:-backend}"

  prepare_directories
  case "$target" in
    backend)
      touch "$BACKEND_LOG_FILE"
      exec tail -n 100 -f "$BACKEND_LOG_FILE"
      ;;
    frontend)
      touch "$FRONTEND_LOG_FILE"
      exec tail -n 100 -f "$FRONTEND_LOG_FILE"
      ;;
    infra)
      if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        fail "Docker daemon 不可用。"
      fi
      cd "$ROOT_DIR"
      exec docker compose logs --tail=100 --follow \
        postgres \
        temporal \
        minio \
        auth-session-worker \
        fixture-worker \
        temporal-worker \
        browser-worker
      ;;
    *)
      fail "未知日志目标：${target}。可选值：backend、frontend、infra。"
      ;;
  esac
}

main() {
  local command_name="${1:-start}"

  case "$command_name" in
    start)
      start_all
      ;;
    stop)
      stop_all
      ;;
    restart)
      stop_all
      start_all
      ;;
    seed)
      seed_only
      ;;
    status)
      show_status
      ;;
    logs)
      follow_logs "${2:-backend}"
      ;;
    help | --help | -h)
      usage
      ;;
    *)
      usage >&2
      fail "未知命令：$command_name"
      ;;
  esac
}

main "$@"
