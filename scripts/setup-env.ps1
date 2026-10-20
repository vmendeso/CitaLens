$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envPath) { Write-Output '.env já existe; preservado.'; exit 0 }
$envText = [IO.File]::ReadAllText((Join-Path $projectRoot '.env.example'))
$generator = [Security.Cryptography.RandomNumberGenerator]::Create()
foreach ($secretName in @('POSTGRES_PASSWORD', 'API_DB_PASSWORD', 'KNOWLEDGE_DB_PASSWORD', 'UPLOAD_DB_PASSWORD', 'RABBITMQ_PASSWORD', 'INTERNAL_TOKEN', 'JWT_SECRET', 'WEAVIATE_API_KEY', 'SEARXNG_SECRET_KEY')) {
    $secretBytes = New-Object byte[] 32
    $generator.GetBytes($secretBytes)
    $secretValue = [BitConverter]::ToString($secretBytes).Replace('-', '').ToLowerInvariant()
    $envText = $envText.Replace("$secretName=CHANGE_ME", "$secretName=$secretValue")
}
$generator.Dispose()
[IO.File]::WriteAllText($envPath, $envText, (New-Object Text.UTF8Encoding $false))
Write-Output '.env criado com segredos locais aleatórios.'
