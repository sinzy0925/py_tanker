#!/usr/bin/env bash
# INSTALL_CHROME: 1 で Linux 時に Google Chrome（apt）と .venv 内の Playwright chromium を入れる。0 または export INSTALL_CHROME=0 でスキップ。
INSTALL_CHROME="${INSTALL_CHROME:-1}"

# run_tmux_cdp0.sh — Google Cloud Shell 等で tmux セッション内から cdp0 パイプラインを実行する。
#
# 使い方（例）:
#   chmod +x run_tmux_cdp0.sh
#   ./run_tmux_cdp0.sh
#
# ホーム（例: /home/USER）にいて実行したい場合:
#   リポジトリを ~/py_tanker に置き、上と同様に ~/run_tmux_cdp0.sh を実行するか、
#   bash ~/py_tanker/run_tmux_cdp0.sh  （カレントはホームのまま）
#
# リポジトリの「親ディレクトリ」にいて実行する場合:
#   bash py_tanker/run_tmux_cdp0.sh
#   リポジトリルートは CDP0_REPO_ROOT / スクリプト位置 / ~/py_tanker の順で解決する。
#
# 環境変数:
#   INSTALL_CHROME     1: Linux で Chrome / Playwright 取得を試す（既定: 1）
#   TMUX_CDP0_SESSION  tmux セッション名（既定: cdp0）
#   CDP0_REPO_ROOT     明示的にリポジトリルートを指定（通常は不要）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# リポジトリルート: CDP0_REPO_ROOT > スクリプト直下 > スクリプト直下の py_tanker/ > ~/py_tanker（ホーム固定）
resolve_repo_root() {
  if [[ -n "${CDP0_REPO_ROOT:-}" ]] && [[ -f "${CDP0_REPO_ROOT}/cdp0_run_cdp_pipeline.py" ]]; then
    (cd "${CDP0_REPO_ROOT}" && pwd)
    return 0
  fi
  if [[ -f "${SCRIPT_DIR}/cdp0_run_cdp_pipeline.py" ]]; then
    echo "${SCRIPT_DIR}"
    return 0
  fi
  if [[ -f "${SCRIPT_DIR}/py_tanker/cdp0_run_cdp_pipeline.py" ]]; then
    (cd "${SCRIPT_DIR}/py_tanker" && pwd)
    return 0
  fi
  if [[ -n "${HOME:-}" ]] && [[ -f "${HOME}/py_tanker/cdp0_run_cdp_pipeline.py" ]]; then
    (cd "${HOME}/py_tanker" && pwd)
    return 0
  fi
  return 1
}

if ! REPO_ROOT="$(resolve_repo_root)"; then
  echo "ERROR: cdp0_run_cdp_pipeline.py が見つかりません。" >&2
  echo "  例: cd py_tanker && ./run_tmux_cdp0.sh" >&2
  echo "  または: export CDP0_REPO_ROOT=/home/.../py_tanker" >&2
  exit 1
fi

install_linux_chrome_and_playwright() {
  [[ "${INSTALL_CHROME}" == "1" ]] || return 0
  [[ "$(uname -s)" == Linux ]] || return 0

  local SUDO=sudo
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
  elif ! command -v sudo >/dev/null 2>&1; then
    echo "WARN: sudo なしのため Chrome の apt インストールをスキップします。" >&2
    SUDO=""
  fi

  if [[ -n "${SUDO}" ]] || [[ "$(id -u)" -eq 0 ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      echo "[INSTALL_CHROME] apt: google-chrome-stable をインストールします ..."
      set +e
      ${SUDO} apt-get update -qq
      ${SUDO} apt-get install -y wget ca-certificates gnupg
      wget -q -O /tmp/google-chrome-key.pub https://dl.google.com/linux/linux_signing_key.pub
      ${SUDO} install -d -m0755 /usr/share/keyrings
      ${SUDO} gpg --dearmor --yes -o /usr/share/keyrings/google-chrome.gpg /tmp/google-chrome-key.pub 2>/dev/null || true
      if [[ -f /usr/share/keyrings/google-chrome.gpg ]]; then
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | ${SUDO} tee /etc/apt/sources.list.d/google-chrome.list >/dev/null
      else
        cat /tmp/google-chrome-key.pub | ${SUDO} apt-key add - 2>/dev/null || true
        echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | ${SUDO} tee /etc/apt/sources.list.d/google-chrome.list >/dev/null
      fi
      ${SUDO} apt-get update -qq
      ${SUDO} apt-get install -y google-chrome-stable
      set -e
    else
      echo "WARN: apt-get がありません。Chrome の自動インストールをスキップします。" >&2
    fi
  fi

  # shellcheck disable=SC2164
  cd "$REPO_ROOT"
  if [[ -f .venv/bin/activate ]]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
    if python -c "import playwright" 2>/dev/null; then
      echo "[INSTALL_CHROME] playwright install-deps / install chromium ..."
      set +e
      python -m playwright install-deps chromium
      set -e
      python -m playwright install chromium
    else
      echo "WARN: venv に playwright がありません。pip install -r requirements.txt 後に再実行してください。" >&2
    fi
  else
    echo "WARN: .venv がありません。先に venv と pip install 後、手動で: python -m playwright install chromium" >&2
  fi
}

set +e
install_linux_chrome_and_playwright
set -e

SESSION="${TMUX_CDP0_SESSION:-cdp0}"

if command -v tmux >/dev/null 2>&1; then
  :
else
  echo "ERROR: tmux not installed. e.g. sudo apt-get install -y tmux" >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

# -c: セッションの開始ディレクトリ（tmux 2.0+）
tmux new-session -ds "$SESSION" -c "$REPO_ROOT" bash -lc '
  set +e
  if [[ -f .venv/bin/activate ]]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
    python cdp0_run_cdp_pipeline.py
  else
    python3 cdp0_run_cdp_pipeline.py
  fi
  ec=$?
  echo "[cdp0_run_cdp_pipeline.py exit code: ${ec}]"
  exec bash -l
'

echo "tmux session started: ${SESSION}"
echo "Attach: tmux attach -t ${SESSION}"
