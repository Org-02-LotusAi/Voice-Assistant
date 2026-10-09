<#
Renders the synthetic seed voices and the human test utterances with Windows SAPI (System.Speech).
  assets/voices/ava_warm.wav     Microsoft Zira  (+ .txt transcript)
  assets/voices/james_calm.wav   Microsoft David (+ .txt transcript)
  assets/test_audio/*.wav        "human" utterances for the Task 10 acceptance harness
                                 voice: Microsoft Mark if installed (a voice distinct from the identity voices),
                                 else Microsoft David at a different rate.
All output is mono 16-bit PCM. Windows only (SAPI). Re-running overwrites the files.
#>
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$root = Split-Path -Parent $PSScriptRoot
$voicesDir = Join-Path $root 'assets/voices'
$testDir = Join-Path $root 'assets/test_audio'
New-Item -ItemType Directory -Force -Path $voicesDir, $testDir | Out-Null

$installed = (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() |
    ForEach-Object { $_.VoiceInfo.Name }

# Prefers the classic "<Name> Desktop" voice, which exists on every Windows edition and in both PowerShell flavours.
function Find-Voice([string]$like) {
    $m = @($installed | Where-Object { $_ -like $like })
    $desktop = $m | Where-Object { $_ -like '* Desktop' } | Select-Object -First 1
    if ($desktop) { return $desktop }
    return ($m | Select-Object -First 1)
}

# 16 kHz mono 16-bit for seed voices is plenty for cloning; 16 kHz for test audio matches Moonshine input.
function Render([string]$voice, [int]$rate, [string]$text, [string]$path, [int]$hz) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synth.SelectVoice($voice)
        $synth.Rate = $rate
        $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
            $hz, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
        $synth.SetOutputToWaveFile($path, $fmt)
        $synth.Speak($text)
    } finally {
        $synth.SetOutputToNull()
        $synth.Dispose()
    }
    $bytes = (Get-Item $path).Length
    $secs = [math]::Round(($bytes - 44) / ($hz * 2), 1)
    Write-Host ("{0}  voice={1} rate={2} {3}s" -f $path, $voice, $rate, $secs)
}

$zira = Find-Voice 'Microsoft Zira*'
$david = Find-Voice 'Microsoft David*'
if (-not $zira -or -not $david) { throw "Microsoft Zira and Microsoft David SAPI voices are required (found: $($installed -join ', '))" }

$avaText = "Hello, thanks for calling. I am happy to walk you through how our service works, answer any questions you have, and make sure you leave this conversation knowing exactly what to expect from us."
$jamesText = "Good afternoon. I will take a moment to explain how our platform fits into your existing workflow, and I will be glad to go through the details at whatever pace is comfortable for you."

Render $zira 0 $avaText (Join-Path $voicesDir 'ava_warm.wav') 24000
Render $david 0 $jamesText (Join-Path $voicesDir 'james_calm.wav') 24000
[IO.File]::WriteAllText((Join-Path $voicesDir 'ava_warm.txt'), $avaText, (New-Object Text.UTF8Encoding $false))
[IO.File]::WriteAllText((Join-Path $voicesDir 'james_calm.txt'), $jamesText, (New-Object Text.UTF8Encoding $false))

# Task 10 human test utterances. Third voice (Mark) if present, else David at rate +2.
# NOTE: Microsoft Mark is a OneCore voice: it is only visible to PowerShell 7 (pwsh), not Windows PowerShell 5.1.
$mark = Find-Voice 'Microsoft Mark*'
if ($mark) { $testVoice = $mark; $testRate = 0 } else { $testVoice = $david; $testRate = 2 }

$utterances = [ordered]@{
    'q_name'           = 'What is your name?'
    'q_company'        = 'Who do you work for?'
    'q_role'           = 'What is your role?'
    'q_greeting'       = 'Hello, can you hear me?'
    'q_long_answer'    = 'Please explain in detail how your service works, from the first call all the way through to the final report, and take your time.'
    'q_interrupt'      = 'Sorry, stop there. Can you just tell me the price?'
    'turn_01'          = 'Hi, I would like to know what your company actually does.'
    'turn_02'          = 'Who are your typical customers?'
    'turn_03'          = 'How long does it take to get started?'
    'turn_04'          = 'Do you integrate with the tools we already use?'
    'turn_05'          = 'What does onboarding look like for a small team?'
    'turn_06'          = 'How do you handle security and privacy?'
    'turn_07'          = 'Can I get a trial before committing?'
    'turn_08'          = 'What kind of support do you offer?'
    'turn_09'          = 'How is pricing structured?'
    'turn_10'          = 'Great, what would be the best next step for us?'
}
foreach ($k in $utterances.Keys) {
    $p = Join-Path $testDir "$k.wav"
    Render $testVoice $testRate $utterances[$k] $p 16000
    [IO.File]::WriteAllText((Join-Path $testDir "$k.txt"), $utterances[$k], (New-Object Text.UTF8Encoding $false))
}
Write-Host "Test-audio voice: $testVoice (rate $testRate)"
