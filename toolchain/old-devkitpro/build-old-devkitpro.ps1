param(
    [string]$Target = ".",
    [string]$Image = "netslug-old-devkitpro:r27-libogc-1.8.12",
    [string]$MakeTarget = "release"
)

$ErrorActionPreference = "Stop"

$toolRoot = Resolve-Path $PSScriptRoot
$repoRoot = Resolve-Path (Join-Path $toolRoot "..\..")
$targetPath = Resolve-Path (Join-Path $repoRoot $Target)

docker build -f (Join-Path $toolRoot "Dockerfile") -t $Image $toolRoot
docker run --rm -v "${targetPath}:/work" -w /work $Image make $MakeTarget
