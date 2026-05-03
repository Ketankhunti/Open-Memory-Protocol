#!/usr/bin/env pwsh
# Sync the docs site's OpenAPI mirror with the normative spec source.
# Run this whenever you edit spec/omp-0.1.openapi.yaml.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Copy-Item `
    -LiteralPath (Join-Path $repoRoot "spec/omp-0.1.openapi.yaml") `
    -Destination (Join-Path $repoRoot "docs/api-reference/openapi.yaml") `
    -Force
Write-Host "synced: docs/api-reference/openapi.yaml <- spec/omp-0.1.openapi.yaml"
