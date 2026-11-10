#!/usr/bin/env bash
# End to end inside the image (read-only root, no capabilities, no network): data with injected faults is generated into
# a volume, checked, profiled, turned into suggested rules, benchmarked, and the result files are checked. Needs Docker.
# Usage: tools/docker_e2e.sh [image]
set -euo pipefail
IMAGE="${1:-dqcheck}"
VOL="dqcheck-e2e-$$"
TMP="$(mktemp -d)"
trap 'docker volume rm -f "$VOL" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

[ "$(docker run --rm --entrypoint id "$IMAGE" -u)" != 0 ]   # not root
docker volume create "$VOL" >/dev/null
docker run --rm --user 0 -v "$VOL":/data --entrypoint chown "$IMAGE" 10001 /data

RUN=(docker run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --network none -v "$VOL":/data "$IMAGE")
cat_file() { docker run --rm --entrypoint cat -v "$VOL":/data "$IMAGE" "$1" > "$2"; }

"${RUN[@]}" generate --rows 3000 --seed 3 --format both --out /data/d >/dev/null
"${RUN[@]}" generate --rows 3000 --seed 3 --format csv --out /data/c >/dev/null

# faults were injected, so the run fails (exit 1) and says which rules found them; the output is kept in $out
set +e; out="$("${RUN[@]}" run /data/d/rules.yml --out /data/out)"; rc=$?; set -e
[ "$rc" = 1 ]
echo "$out" >&2
for want in 'FAIL  O01' 'WARN  C03' 'PASS  C01' '16 rules: 5 passed, 1 warnings, 10 failed, 0 could not run' 'written: /data/out/report.json'; do
  grep -q -e "$want" <<<"$out"
done
cat_file /data/out/report.json "$TMP/report.json"
cat_file /data/out/report.md "$TMP/report.md"
grep -q '"fail": 10' "$TMP/report.json"
grep -q '"failing_rows": 60' "$TMP/report.json"                  # 2% of 3000 orders: the duplicated order ids
head -n 1 "$TMP/report.md" | grep -q '^# Data quality report'

# the same data as CSV gives the same answer
set +e; out="$("${RUN[@]}" run /data/c/rules.yml)"; rc=$?; set -e
[ "$rc" = 1 ]
grep -q '16 rules: 5 passed, 1 warnings, 10 failed' <<<"$out"

"${RUN[@]}" render /data/out/report.json -o /data/out/again.md >/dev/null
cat_file /data/out/again.md "$TMP/again.md"
cmp "$TMP/again.md" "$TMP/report.md"                              # the report is rebuilt exactly from the JSON

out="$("${RUN[@]}" profile /data/d/orders.parquet)"
grep -q 'order_id' <<<"$out"
"${RUN[@]}" suggest /data/d/orders.parquet /data/d/customers.parquet -o /data/s.yml >/dev/null
"${RUN[@]}" run /data/s.yml >/dev/null                            # rules written from the data pass on that data

"${RUN[@]}" bench --sizes 300,600 --repeat 1 --out /data/bench >/dev/null 2>&1
cat_file /data/bench/bench.json "$TMP/bench.json"
cat_file /data/bench/summary.png "$TMP/summary.png"
grep -q '"verified": true' "$TMP/bench.json"
! grep -q '"verified": false' "$TMP/bench.json"
[ "$(head -c 4 "$TMP/summary.png" | tail -c 3)" = "PNG" ]

set +e; "${RUN[@]}" run /data/nope.yml >/dev/null 2>&1; rc=$?; set -e   # a missing rules file is exit code 2
[ "$rc" = 2 ]
echo "docker end-to-end: OK"
