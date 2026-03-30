#!/usr/bin/env bash
# run_tmux_cdp0.sh と同一ロジックで Chrome / Playwright をセットアップするだけ（tmux なし・cdp0 は起動しない）。
# 実体は CDP0_SKIP_PIPELINE=1 の run_tmux_cdp0.sh。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${SCRIPT_DIR}/run_tmux_cdp0.sh"
if [[ ! -f "${RUNNER}" ]]; then
  if [[ -n "${HOME:-}" ]] && [[ -f "${HOME}/py_tanker/run_tmux_cdp0.sh" ]]; then
    RUNNER="${HOME}/py_tanker/run_tmux_cdp0.sh"
  else
    echo "ERROR: run_tmux_cdp0.sh が見つかりません（${SCRIPT_DIR} または ~/py_tanker）。" >&2
    exit 1
  fi
fi

export CDP0_SKIP_PIPELINE="${CDP0_SKIP_PIPELINE:-1}"
exec bash "${RUNNER}" "$@"
