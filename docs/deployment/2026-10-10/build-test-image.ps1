param(
    [string]$Image = 'kiro-rs:billing-test-20261010',
    [string]$OutputDirectory = (Join-Path $env:TEMP 'kiro-billing-release-20261010')
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$source = Join-Path $OutputDirectory 'source'
New-Item -ItemType Directory -Force -Path $source | Out-Null
# A source allowlist keeps runtime files, Git history and local artifacts out of Docker.
$files = @('Cargo.toml', 'Cargo.lock', 'Dockerfile', '.dockerignore')
$files += git -C $repo ls-files src admin-ui
if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate tracked source files' }
$files += 'docs/deployment/2026-10-10/Dockerfile.billing-test'
foreach ($relative in $files) {
    $destination = Join-Path $source $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo $relative) -Destination $destination
}
docker build --platform linux/amd64 --progress plain `
    -f (Join-Path $source 'docs/deployment/2026-10-10/Dockerfile.billing-test') `
    -t $Image $source
if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
$archive = Join-Path $OutputDirectory 'image.tar'
docker save -o $archive $Image
if ($LASTEXITCODE -ne 0) { throw 'Image export failed' }
Get-FileHash -Algorithm SHA256 -LiteralPath $archive
docker image inspect $Image --format '{{.Id}}'
