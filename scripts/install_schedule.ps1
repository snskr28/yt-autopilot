# Registers the yt-autopilot tasks in Windows Task Scheduler (folder \yt-autopilot\).
# Runs as the current user, only while logged on (Claude Code and the YouTube token live in this profile).
#
# Missed runs: Windows' StartWhenAvailable proved unreliable after sleep, so every task also
# fires a few minutes after sign-in and after unlocking (waking from sleep). That is safe
# because `generate --ensure` only writes a script when the next slot is due and has none, and
# `produce` only works on approved jobs that aren't uploaded yet. Times are staggered so two
# tasks don't hit the run lock together.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1            # install / update
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -Remove    # remove all
param([switch]$Remove)

$folder = '\yt-autopilot\'
$cmd = Join-Path $PSScriptRoot 'task.cmd'
$project = Split-Path $PSScriptRoot -Parent

if ($Remove) {
    Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    Write-Output 'Removed all yt-autopilot tasks.'
    return
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Daily([string]$at) { New-ScheduledTaskTrigger -Daily -At $at }

function AtLogon([string]$delay) {
    $t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $t.Delay = $delay
    $t
}

function AtUnlock([string]$delay) {
    # session unlock (state change 8): what happens when the PC wakes from sleep and you sign back in
    $class = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger
    $t = New-CimInstance -CimClass $class -ClientOnly
    $t.StateChange = 8
    $t.UserId = $env:USERNAME
    $t.Delay = $delay
    $t.Enabled = $true
    $t
}

$tasks = @(
    @{ Name = 'generate-short'; Args = 'generate --type short --ensure'
       Triggers = @((Daily '09:00'), (Daily '13:00'), (AtLogon 'PT3M'), (AtUnlock 'PT3M')) },
    @{ Name = 'generate-long';  Args = 'generate --type long --ensure'
       Triggers = @((Daily '09:20'), (Daily '13:20'), (AtLogon 'PT10M'), (AtUnlock 'PT10M')) },
    @{ Name = 'produce';        Args = 'produce'
       Triggers = @((Daily '14:00'), (Daily '21:00'), (AtLogon 'PT25M'), (AtUnlock 'PT25M')) },
    @{ Name = 'weekly-stats';   Args = 'stats'
       Triggers = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '20:00') }
)

# tasks from the first version of this script
foreach ($old in 'produce-1400', 'produce-2100') {
    Unregister-ScheduledTask -TaskPath $folder -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
}

foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute $cmd -Argument $t.Args -WorkingDirectory $project
    Register-ScheduledTask -TaskPath $folder -TaskName $t.Name -Action $action -Trigger $t.Triggers `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output ("Registered {0,-15} -> task.cmd {1}  ({2} triggers)" -f $t.Name, $t.Args, $t.Triggers.Count)
}
Get-ScheduledTask -TaskPath $folder | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-Table -AutoSize
