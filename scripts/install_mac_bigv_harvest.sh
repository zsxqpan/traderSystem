#!/bin/bash
# 本机（macOS）注册每天 17:10 的大V慢速采集。不 commit / 不 push。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tradersystem.bigv-harvest"
AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENT_DIR/${LABEL}.plist"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/myenv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "找不到项目虚拟环境 python: $ROOT/.venv 或 $ROOT/myenv" >&2
  exit 1
fi
mkdir -p "$AGENT_DIR" "$ROOT/logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>-u</string>
    <string>${ROOT}/scripts/run_job.py</string>
    <string>big_v_harvest</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>17</integer>
    <key>Minute</key>
    <integer>10</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/big_v_harvest.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/big_v_harvest.log</string>
  <key>RunAtLoad</key>
  <false/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${ROOT}/.venv/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF
UID_NUM="$(id -u)"
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
launchctl enable "gui/${UID_NUM}/${LABEL}"
echo "已注册 ${LABEL}：每天 17:10 跑 scripts/run_job.py big_v_harvest"
launchctl print "gui/${UID_NUM}/${LABEL}" | awk '/state =|runs =|path =/'
