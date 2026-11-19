#!/bin/sh
# Run a command with a hard memory cap, so a runaway job is stopped on its own instead of the
# kernel's out-of-memory killer picking a process anywhere on the machine.
#
# Usage: scripts/capped.sh <limit, e.g. 16G> <command> [args...]
# Uses a transient systemd user scope (cgroup); swap is not allowed to extend the limit.
set -eu
limit=$1
shift
exec systemd-run --user --scope --quiet -p MemoryMax="$limit" -p MemorySwapMax=0 "$@"
