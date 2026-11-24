#!/bin/sh
# Measure bcmdhd TCP ACK/PSH pool exhaustion over a fixed window, ON THE DEVICE.
#
# Why this exists: the mount's radio cannot sustain bulk transfer (issue #171) and the SSH
# session dies under load - exactly when errors accumulate. So an interactive A/B destroys its
# own measurement channel. Run this detached; it writes results to a file and never depends on
# the session that started it surviving.
#
# dmesg | grep -c is NOT cumulative (the ring wraps). Only a delta over a fixed window means
# anything, which is what this produces.
#
# Usage:  pool-window.sh [seconds] [param value to test, or "keep"]
#   ssh root@192.168.0.1 'sh -s 300 1' < pool-window.sh    # arm proptx=1 for 300 s
#   nohup ... >/dev/null 2>&1 &   to detach; then read /tmp/pool-window.txt
set -e
SECS=${1:-180}
VAL=${2:-keep}
P=/sys/module/bcmdhd/parameters/disable_proptx
OUT=/tmp/pool-window.txt
SIG='tdata_psh_info'

ORIG=$(cat "$P" 2>/dev/null || echo unknown)
restore() { [ "$VAL" != keep ] && [ -n "$ORIG" ] && [ "$ORIG" != unknown ] && echo "$ORIG" > "$P"; }
trap restore EXIT INT TERM

[ "$VAL" != keep ] && echo "$VAL" > "$P"

b=$(dmesg | grep -c "$SIG")
u0=$(cut -d. -f1 /proc/uptime)
sleep "$SECS"
a=$(dmesg | grep -c "$SIG")
u1=$(cut -d. -f1 /proc/uptime)

{
  echo "ts=$(date +%s) param=$P now=$(cat "$P" 2>/dev/null) requested=$VAL orig=$ORIG"
  echo "base=$b after=$a delta=$((a-b)) secs=$((u1-u0))"
  echo "assoc: $(tail -1 /proc/net/wireless 2>/dev/null)"
} >> "$OUT"
echo done
