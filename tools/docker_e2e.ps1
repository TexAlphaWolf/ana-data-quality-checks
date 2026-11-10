# End to end inside the image, from Windows PowerShell: data with injected faults is generated into a volume, checked,
# profiled, turned into suggested rules and benchmarked, and the result files are checked (read-only root, no
# capabilities, no network). Needs Docker Desktop.
# Usage: .\tools\docker_e2e.ps1 [-Image dqcheck]
param([string]$Image = "dqcheck")
$ErrorActionPreference = "Stop"

$id = Get-Random
$vol = "dqcheck-e2e-$id"
$secure = @("--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--network", "none", "-v", "${vol}:/data")

# Windows PowerShell 5.1 turns a program's message on stderr into an error under "Stop": the commands that print
# progress on stderr or are expected to fail run with errors not terminating, and only their exit code is looked at.
function Invoke-DqCode([int[]]$Allowed, [string[]]$DqArgs) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = docker run --rm @secure $Image @DqArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($Allowed -notcontains $code) { throw "dqcheck $DqArgs exited with $code : $out" }
    ($out | Out-String)
}
function Invoke-Dq([string[]]$DqArgs) { Invoke-DqCode @(0) $DqArgs }
function Read-Volume($path) {
    $out = docker run --rm --entrypoint cat -v "${vol}:/data" $Image $path 2>$null
    if ($LASTEXITCODE -ne 0) { throw "cannot read $path" }
    ($out | Out-String)
}
function Assert-Contains($text, $needle) { if ($text -notmatch [regex]::Escape($needle)) { throw "expected '$needle' in: $text" } }

try {
    docker volume create $vol | Out-Null
    docker run --rm --user 0 -v "${vol}:/data" --entrypoint chown $Image 10001 /data
    if ($LASTEXITCODE -ne 0) { throw "could not prepare the volume" }

    Invoke-Dq @("generate", "--rows", "3000", "--seed", "3", "--format", "both", "--out", "/data/d") | Out-Null
    Invoke-Dq @("generate", "--rows", "3000", "--seed", "3", "--format", "csv", "--out", "/data/c") | Out-Null

    # faults were injected, so the run fails (exit 1) and says which rules found them
    $run = Invoke-DqCode @(1) @("run", "/data/d/rules.yml", "--out", "/data/out")
    foreach ($want in "FAIL  O01", "WARN  C03", "PASS  C01", "16 rules: 5 passed, 1 warnings, 10 failed, 0 could not run", "written: /data/out/report.json") { Assert-Contains $run $want }
    $json = Read-Volume "/data/out/report.json"
    Assert-Contains $json '"fail": 10'
    Assert-Contains $json '"failing_rows": 60'
    $md = Read-Volume "/data/out/report.md"
    Assert-Contains $md "# Data quality report"

    Assert-Contains (Invoke-DqCode @(1) @("run", "/data/c/rules.yml")) "16 rules: 5 passed, 1 warnings, 10 failed"

    Invoke-Dq @("render", "/data/out/report.json", "-o", "/data/out/again.md") | Out-Null
    if ((Read-Volume "/data/out/again.md") -ne $md) { throw "the report rebuilt from the JSON is not identical" }

    Assert-Contains (Invoke-Dq @("profile", "/data/d/orders.parquet")) "order_id"
    Invoke-Dq @("suggest", "/data/d/orders.parquet", "/data/d/customers.parquet", "-o", "/data/s.yml") | Out-Null
    Invoke-Dq @("run", "/data/s.yml") | Out-Null

    Invoke-Dq @("bench", "--sizes", "300,600", "--repeat", "1", "--out", "/data/bench") | Out-Null
    $bench = Read-Volume "/data/bench/bench.json"
    Assert-Contains $bench '"verified": true'
    if ($bench -match '"verified": false') { throw "a benchmark count differs from the manifest" }

    Invoke-DqCode @(2) @("run", "/data/nope.yml") | Out-Null
    Write-Host "docker end-to-end: OK"
}
finally {
    $ErrorActionPreference = "Continue"
    docker volume rm -f $vol 2>&1 | Out-Null
}
