#!/usr/bin/env bash
# Google Cloud Shell など: tmux 内で imo_match_stream.py を長時間実行する
#
# 事前:
#   - リポジトリを配置し、このファイルがあるディレクトリで実行
#   - pip install -r requirements.txt（または venv 有効化）
#   - .env に AISSTREAM_API_KEY
#
# 使い方:
#   chmod +x run_imo_match_tmux.sh
#   ./run_imo_match_tmux.sh
# 再接続:
#   tmux attach -t "${TMUX_SESSION:-imo_ais_stream}"

set -euo pipefail

# =============================================================================
# 設定（主にここだけ編集）
# =============================================================================
DURATION_SEC=3600 # 受信する秒数（3600 = 1 時間）

TMUX_SESSION="imo_ais_stream"
IMO_FILE="data/imo_list.csv"
PYTHON_CMD="python3" # Cloud Shell では python3 を推奨

# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux が見つかりません。例: sudo apt-get update && sudo apt-get install -y tmux" >&2
  exit 1
fi

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON_CMD="python"
fi

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  echo "既存セッション $TMUX_SESSION を終了します"
  tmux kill-session -t "$TMUX_SESSION"
fi

echo "起動: tmux セッション '$TMUX_SESSION'（${DURATION_SEC} 秒）"

tmux new-session -d -s "$TMUX_SESSION" \
  env SCRIPT_DIR="$SCRIPT_DIR" PYTHON_CMD="$PYTHON_CMD" IMO_FILE="$IMO_FILE" DURATION_SEC="$DURATION_SEC" \
  bash -c '
    set -uo pipefail
    cd "$SCRIPT_DIR"
    "$PYTHON_CMD" imo_match_stream.py \
      --imo-file "$IMO_FILE" \
      --stamp-out \
      --duration "$DURATION_SEC"
    ec=$?
    echo ""
    echo "--- imo_match_stream 終了 (exit $ec) ---"
    exec bash -l
  '

echo ""
echo "OK. バックグラウンドで実行中です。"
echo ""
echo "セッションに接続するには次を実行:"
echo "  tmux attach -t imo_ais_stream"
echo "（スクリプト先頭で TMUX_SESSION を変えた場合はその名前に合わせる）"
echo "デタッチ: Ctrl+b を押してから d"
