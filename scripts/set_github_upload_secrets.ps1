param(
    [string]$Repo = "epratesti/tiktok-quiz-automation",
    [string]$StatePath = "data/tiktok_state.json"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) nao encontrado. Instale em https://cli.github.com/ ou configure os secrets manualmente."
}

if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "Sessao nao encontrada em $StatePath. Rode primeiro: python scripts/setup_tiktok_session.py"
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $StatePath))
$encoded = [Convert]::ToBase64String($bytes)

$encoded | gh secret set TIKTOK_STORAGE_STATE_B64 --repo $Repo
"false" | gh secret set DRY_RUN --repo $Repo
"true" | gh secret set TIKTOK_UPLOAD_ENABLED --repo $Repo
"edge" | gh secret set VOICE_PROVIDER --repo $Repo
"pt-BR-ThalitaMultilingualNeural" | gh secret set EDGE_TTS_VOICE --repo $Repo
"+5%" | gh secret set EDGE_TTS_RATE --repo $Repo
"+0Hz" | gh secret set EDGE_TTS_PITCH --repo $Repo
"+0%" | gh secret set EDGE_TTS_VOLUME --repo $Repo

Write-Host "Secrets de upload configurados no repositorio $Repo."
