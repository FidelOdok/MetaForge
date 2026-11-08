#!/bin/sh
# CadQuery adapter start-up: run the adapter as uid 1000 (metaforge).
#
# The isolated script runner starts each cadquery.execute_script child as a
# uid from CADQUERY_RUNNER_UIDS. For that the adapter keeps exactly two
# capabilities, CAP_SETUID and CAP_SETGID, as ambient capabilities; nothing
# else. The child drops them all before Python starts (setpriv
# --inh-caps=-all --ambient-caps=-all --no-new-privs).
#
# Started as root (the image default) with those two capabilities granted,
# this drops to uid 1000 and keeps them. Started without them, the adapter
# runs as uid 1000 with none, and execute_script refuses to run while
# CADQUERY_RUNNER_REQUIRE includes "uid". Started as another user already
# (docker run --user), the command runs unchanged.
set -eu

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

# setpriv keeps the environment, and root's HOME is not readable by uid 1000.
export HOME=/home/metaforge USER=metaforge LOGNAME=metaforge

if setpriv --reuid=1000 --regid=1000 --clear-groups \
        --inh-caps=+setuid,+setgid --ambient-caps=+setuid,+setgid -- true 2>/dev/null; then
    exec setpriv --reuid=1000 --regid=1000 --clear-groups \
        --inh-caps=+setuid,+setgid --ambient-caps=+setuid,+setgid -- "$@"
fi

echo "cadquery-adapter: CAP_SETUID/CAP_SETGID are not available; starting as uid 1000" \
    "without them, so the script runner cannot switch uid" >&2
exec setpriv --reuid=1000 --regid=1000 --clear-groups -- "$@"
