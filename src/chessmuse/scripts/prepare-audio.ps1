param([Parameter(Mandatory=$true)][string]$DataDirectory, [string]$ScriptPath = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
if (-not $ScriptPath) { $ScriptPath = Join-Path $DataDirectory 'demo.json' }
$scriptData = Get-Content -LiteralPath $ScriptPath -Raw -Encoding UTF8 | ConvertFrom-Json
$audioDirectory = Join-Path $DataDirectory 'audio'
New-Item -ItemType Directory -Path $audioDirectory -Force | Out-Null
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.SelectVoice('Microsoft Huihui Desktop')
    $speaker.Rate = 1
    foreach ($paragraph in $scriptData.paragraphs) {
        $audioPath = Join-Path $audioDirectory ($paragraph.id + '.wav')
        $speaker.SetOutputToWaveFile($audioPath)
        $speaker.Speak($paragraph.text)
        $speaker.SetOutputToNull()
        Write-Output ('Generated: ' + $paragraph.id)
    }
    $manifest = @{
        voice = 'Microsoft Huihui Desktop'
        paragraphs = @($scriptData.paragraphs | Select-Object id,text)
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $DataDirectory 'audio.json') -Encoding UTF8
} finally {
    $speaker.Dispose()
}
