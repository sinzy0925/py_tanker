#!/usr/bin/env bash
# Chrome / Playwright セットアップのみ（tmux も cdp0 パイプラインも起動しない）。
# run_tmux_cdp0.sh に CDP0_SKIP_PIPELINE=1 を付けて exec する。
#
# パイプラインを tmux なしで動かす場合: CDP0_NO_TMUX=1 ./run_tmux_cdp0.sh

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

# 環境に CDP0_SKIP_PIPELINE=0 が残っていると従来の export では tmux 側に進んでしまうため、常に 1 を付与する
exec env CDP0_SKIP_PIPELINE=1 bash "${RUNNER}" "$@"
