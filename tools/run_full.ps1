# The full measurement on your own computer, in Docker (no network, read-only root, no capabilities), from Windows
# PowerShell: data of growing size is generated, loaded and checked with the whole rule set, and every count is compared
# with the manifest of injected faults.
# Usage: .\tools\run_full.ps1 [-Sizes 100000,1000000,10000000] [-Repeat 5] [-Out results]
# The largest size needs about 2.1 GiB of memory and about 1 GiB of disk while it runs (the generated files go to the
# output folder and are removed); give Docker Desktop at least 4 GiB or leave the largest size out.
param(
    [string]$Sizes = "100000,1000000,10000000",
    [int]$Repeat = 5,
    [string]$Out = "results"
)
# Windows PowerShell 5.1 turns anything a program writes on stderr into an error under "Stop": docker and dqcheck print
# their progress there, so only the exit code is looked at.
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$outFull = (Resolve-Path -LiteralPath $Out).Path

docker build -t dqcheck .
if ($LASTEXITCODE -ne 0) { Write-Host "docker build failed"; exit 1 }

Write-Host "== bench (sizes $Sizes, $Repeat runs each) =="
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --network none -v "${outFull}:/out" dqcheck bench --sizes $Sizes --repeat $Repeat --workdir /out --out /out
if ($LASTEXITCODE -ne 0) { Write-Host "bench failed (exit code $LASTEXITCODE)"; exit 1 }
Write-Host "Done. Send me $Out\bench.json, $Out\bench.md and $Out\summary.png."
