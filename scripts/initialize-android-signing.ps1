[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\private-config\android-signing'),
    [string]$JavaHome = 'C:\Program Files\Java\jdk-21'
)

$ErrorActionPreference = 'Stop'
$keytool = Join-Path $JavaHome 'bin\keytool.exe'
$keystore = Join-Path $OutputDirectory 'pcs-companion-release.jks'
$properties = Join-Path $OutputDirectory 'pcs-companion-signing.properties'
$alias = 'pcs-companion-release'

if (-not (Test-Path -LiteralPath $keytool -PathType Leaf)) {
    throw "keytool was not found at $keytool"
}
if ((Test-Path -LiteralPath $keystore) -or (Test-Path -LiteralPath $properties)) {
    throw 'Android signing credentials already exist; refusing to replace the release identity.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$random = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($random)
$password = [Convert]::ToBase64String($random).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$env:PCS_ANDROID_SIGNING_PASSWORD = $password

try {
    & $keytool -genkeypair -noprompt `
        -keystore $keystore `
        -storetype JKS `
        -storepass:env PCS_ANDROID_SIGNING_PASSWORD `
        -keypass:env PCS_ANDROID_SIGNING_PASSWORD `
        -alias $alias `
        -keyalg RSA `
        -keysize 4096 `
        -sigalg SHA256withRSA `
        -validity 10000 `
        -dname 'CN=PCS Companion, OU=Portable Comm Server, O=Saberhawk09'
    if ($LASTEXITCODE -ne 0) {
        throw "keytool exited with status $LASTEXITCODE"
    }

    $gradleStorePath = $keystore.Replace('\', '/')
    $lines = @(
        "storeFile=$gradleStorePath"
        "storePassword=$password"
        "keyAlias=$alias"
        "keyPassword=$password"
    )
    [IO.File]::WriteAllLines($properties, $lines, [Text.UTF8Encoding]::new($false))

    Write-Host "Created durable Android release keystore: $keystore"
    Write-Host "Created ignored Gradle signing properties: $properties"
    & $keytool -list -v `
        -keystore $keystore `
        -storepass:env PCS_ANDROID_SIGNING_PASSWORD `
        -alias $alias `
        | Select-String 'SHA256:'
} finally {
    Remove-Item Env:\PCS_ANDROID_SIGNING_PASSWORD -ErrorAction SilentlyContinue
    [Array]::Clear($random, 0, $random.Length)
    $password = $null
}
