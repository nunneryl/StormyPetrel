#!/usr/bin/env bash
# Install apt packages on a GitHub runner, retrying a failed attempt before giving up.
#
#   bash scripts/apt_install.sh libeccodes0 unzip
#
# WHY. On 2026-11-11 the 16:28Z full pipeline run (the 13:45 slot's fallback) failed at its
# first step: the runner could not resolve azure.archive.ubuntu.com ("Temporary failure
# resolving"), apt-get install exited 100, and the run fetched nothing. A DNS hiccup on the
# runner usually clears in seconds to a minute or two.
#
# WHAT IT DOES. Up to APT_ATTEMPTS attempts (default 4). Each attempt runs
# `sudo apt-get update -qq` (whose result is ignored: it warns and carries on over an index it
# cannot fetch) and then `sudo apt-get install -y --no-install-recommends PACKAGES`. The first
# install that exits 0 ends the script with 0. Between attempts it waits the next number of
# seconds in APT_WAITS (default "10 30 60": 100 s at most), and logs a ::warning:: line. When the
# last attempt fails it logs one ::error:: line and exits with apt-get's own exit code.
set -u

if [ "$#" -eq 0 ]; then
  echo "::error::apt_install.sh: no packages named"
  exit 2
fi

attempts="${APT_ATTEMPTS:-4}"
read -r -a waits <<< "${APT_WAITS:-10 30 60}"

n=1
while :; do
  sudo apt-get update -qq || true
  sudo apt-get install -y --no-install-recommends "$@"
  code=$?
  if [ "$code" -eq 0 ]; then
    [ "$n" -gt 1 ] && echo "apt-get install of $* succeeded on attempt $n of $attempts"
    exit 0
  fi
  if [ "$n" -ge "$attempts" ]; then
    echo "::error::apt-get install of $* failed on all $attempts attempts (exit $code)"
    exit "$code"
  fi
  wait_s="${waits[$((n - 1))]:-${waits[${#waits[@]}-1]}}"
  echo "::warning::apt-get install of $* failed (exit $code) on attempt $n of $attempts; retrying in ${wait_s} s"
  sleep "$wait_s"
  n=$((n + 1))
done
