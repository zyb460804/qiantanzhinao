#!/usr/bin/env bash
# ============================================================
# 千摊智脑 部署与运维脚本
#
# 用法:
#   ./scripts/deploy.sh <command> [args]
#
# 命令:
#   dev      本地开发模式启动 (SQLite, 零依赖)
#   docker   Docker Compose 生产模式启动 (PostgreSQL + FastAPI)
#   test     运行全部测试 (单元 + 集成)
#   seed     填充演示数据
#   migrate  运行数据库迁移 (Alembic)
#   stop     停止运行中的服务
#   logs     查看服务日志
#   clean    清理构建产物与缓存数据库
#   health   健康检查
#   help     显示帮助
# ============================================================

set -euo pipefail

# ── 路径与配置 ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()   { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

# ── 命令实现 ────────────────────────────────────────────

cmd_dev() {
    log "启动本地开发模式 (SQLite, 零依赖)"
    cd "$BACKEND_DIR"

    # 确保 .env 存在
    if [ ! -f ".env" ]; then
        log "复制 .env.example → .env"
        cp .env.example .env
        warn "请在 .env 中填写 QWEATHER_API_KEY (可选, 不填则用 mock 数据)"
    fi

    # 确保 Python 依赖已安装
    if ! python -c "import fastapi" 2>/dev/null; then
        step "安装 Python 依赖..."
        pip install -r requirements.txt
    fi

    log "启动 FastAPI (http://localhost:8000)"
    log "API 文档: http://localhost:8000/docs"
    log "按 Ctrl+C 停止"
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
}

cmd_docker() {
    log "启动 Docker Compose 生产模式 (PostgreSQL + FastAPI)"
    cd "$PROJECT_ROOT"

    if ! command -v docker &> /dev/null; then
        error "Docker 未安装, 请先安装 Docker Desktop"
        exit 1
    fi

    step "构建并启动容器..."
    docker-compose up -d --build

    log "等待服务就绪..."
    sleep 5

    cmd_health
    log "服务已启动:"
    log "  API:      http://localhost:8000"
    log "  数据库:   localhost:5432 (PostgreSQL)"
    log ""
    log "查看日志: ./scripts/deploy.sh logs"
    log "停止服务: ./scripts/deploy.sh stop"
}

cmd_test() {
    log "运行全部测试"
    cd "$BACKEND_DIR"

    if ! python -c "import pytest" 2>/dev/null; then
        step "安装测试依赖..."
        pip install -r requirements-test.txt
    fi

    step "执行 pytest..."
    python -m pytest tests/ -v "$@"
}

cmd_seed() {
    log "填充演示数据"
    cd "$BACKEND_DIR"

    step "运行 seed_db.py..."
    python scripts/seed_db.py

    log "演示数据填充完成 (1 商户 + 10 商品 + 30 天环境 + 284 库存流水)"
}

cmd_migrate() {
    log "运行数据库迁移 (Alembic)"
    cd "$BACKEND_DIR"

    if [ "${1:-}" = "create" ]; then
        shift
        step "创建新迁移: $*"
        alembic revision --autogenerate -m "$*"
    else
        step "执行迁移到最新版本..."
        alembic upgrade head
    fi
}

cmd_stop() {
    log "停止运行中的服务"
    cd "$PROJECT_ROOT"

    # 停止 Docker 容器
    if docker-compose ps -q 2>/dev/null | grep -q .; then
        step "停止 Docker 容器..."
        docker-compose down
    fi

    # 停止本地 uvicorn (仅当前用户进程)
    if pgrep -f "uvicorn app.main" &> /dev/null; then
        step "停止本地 uvicorn 进程..."
        pkill -f "uvicorn app.main" || true
    fi

    log "服务已停止"
}

cmd_logs() {
    log "查看服务日志 (Ctrl+C 退出)"
    cd "$PROJECT_ROOT"

    if docker-compose ps -q 2>/dev/null | grep -q .; then
        docker-compose logs -f "$@"
    else
        error "没有运行中的 Docker 服务"
        warn "本地开发模式下日志直接输出在终端"
    fi
}

cmd_clean() {
    warn "清理构建产物与缓存数据库"
    cd "$PROJECT_ROOT"

    # Python 缓存
    find "$BACKEND_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$BACKEND_DIR" -name "*.pyc" -delete 2>/dev/null || true
    rm -rf "$BACKEND_DIR/.pytest_cache"

    # SQLite 开发数据库
    rm -f "$BACKEND_DIR/qiantan_dev.db"

    # 上传文件
    rm -rf "$BACKEND_DIR/uploads"

    log "清理完成"
}

cmd_health() {
    log "健康检查..."
    local max_retries=10
    local retry=0

    while [ $retry -lt $max_retries ]; do
        if curl -sf "http://localhost:8000/api/v1/health" &> /dev/null; then
            log "服务健康 ✓ (http://localhost:8000/api/v1/health)"
            curl -s "http://localhost:8000/api/v1/health" | python -m json.tool 2>/dev/null || true
            return 0
        fi
        retry=$((retry + 1))
        printf "."
        sleep 2
    done

    echo ""
    error "服务未响应 (重试 $max_retries 次后放弃)"
    error "请检查日志: ./scripts/deploy.sh logs"
    return 1
}

cmd_help() {
    cat << 'EOF'
千摊智脑 部署脚本
==================

用法: ./scripts/deploy.sh <command> [args]

命令:
  dev             本地开发模式 (SQLite, uvicorn --reload)
  docker          Docker Compose 生产模式 (PostgreSQL + FastAPI)
  test            运行全部测试 (单元 + 集成)
                  附加参数透传给 pytest, 如: ./scripts/deploy.sh test -k voice
  seed            填充演示数据 (1商户 + 10商品 + 30天数据)
  migrate         运行数据库迁移到最新版本
  migrate create  创建新迁移: ./scripts/deploy.sh migrate create "添加字段"
  stop            停止运行中的服务 (Docker + 本地 uvicorn)
  logs            查看服务日志 (实时跟踪)
  clean           清理缓存/测试数据库/上传文件
  health          健康检查 (轮询 /api/v1/health)
  help            显示此帮助

快速开始:
  本地开发:  ./scripts/deploy.sh dev
  Docker:    ./scripts/deploy.sh docker
  测试:      ./scripts/deploy.sh test
EOF
}

# ── 入口 ────────────────────────────────────────────────

main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        dev)      cmd_dev "$@" ;;
        docker)   cmd_docker "$@" ;;
        test)     cmd_test "$@" ;;
        seed)     cmd_seed "$@" ;;
        migrate)  cmd_migrate "$@" ;;
        stop)     cmd_stop "$@" ;;
        logs)     cmd_logs "$@" ;;
        clean)    cmd_clean "$@" ;;
        health)   cmd_health "$@" ;;
        help|-h|--help) cmd_help ;;
        *)
            error "未知命令: $cmd"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
