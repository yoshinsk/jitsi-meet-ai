# File: ops/generate-japanese-announcements.ps1
# Summary: Generate reviewed Japanese announcement WAV files using the installed Windows Japanese voice without an API key.
param([string]$OutputDirectory = 'assets/announcements')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$destination = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDirectory))
[IO.Directory]::CreateDirectory($destination) | Out-Null
$scriptText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'japanese-announcements.json') -Encoding UTF8 -Raw | ConvertFrom-Json
$voice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $voice.SelectVoice('Microsoft Haruka Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(48000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    # Save uncompressed originals; the server installer produces browser-compatible MP3 and Opus files.
    foreach ($entry in $scriptText.PSObject.Properties) {
        if ($entry.Name.StartsWith('_')) { continue }
        $voice.SetOutputToWaveFile((Join-Path $destination ($entry.Name + '.wav')), $format)
        $voice.Speak($entry.Value)
        $voice.SetOutputToNull()
    }
} finally {
    $voice.Dispose()
}
Write-Output 'JAPANESE_ANNOUNCEMENTS_GENERATED'
