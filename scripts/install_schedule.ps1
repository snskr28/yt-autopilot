# Registers the yt-autopilot tasks in Windows Task Scheduler (folder \yt-autopilot\).
# Runs as the current user, only while logged on (Claude Code and the YouTube token live in this profile).
#
#   tick          every 30 minutes, and 3 minutes after sign-in or unlock (waking from sleep):
#                 writes any script due tomorrow (generate --ensure), then makes and uploads
#                 whatever is approved (produce). Cheap when nothing is due, so missed times
#                 simply catch up on the next tick.
#   weekly-stats  Sunday 20:00
#
# Runs are launched hidden (task-hidden.vbs), so no console window flashes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1            # install / update
#   powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1 -Remove    # remove all
param([switch]$Remove)

$folder = '\yt-autopilot\'
$launcher = Join-Path $PSScriptRoot 'task-hidden.vbs'
$project = Split-Path $PSScriptRoot -Parent

if ($Remove) {
    Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    Write-Output 'Removed all yt-autopilot tasks.'
    return
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Every30Minutes {
    $t = New-ScheduledTaskTrigger -Daily -At '00:00'
    $t.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
    $t
}

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
    @{ Name = 'tick';         Args = 'tick'
       Triggers = @((Every30Minutes), (AtLogon 'PT3M'), (AtUnlock 'PT3M')) },
    @{ Name = 'weekly-stats'; Args = 'stats'
       Triggers = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '20:00') }
)

# tasks from earlier versions of this script
foreach ($old in 'produce-1400', 'produce-2100', 'produce', 'generate-short', 'generate-long') {
    Unregister-ScheduledTask -TaskPath $folder -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
}

foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}" {1}' -f $launcher, $t.Args) `
        -WorkingDirectory $project
    Register-ScheduledTask -TaskPath $folder -TaskName $t.Name -Action $action -Trigger $t.Triggers `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output ("Registered {0,-13} -> {1}  ({2} triggers)" -f $t.Name, $t.Args, $t.Triggers.Count)
}
Get-ScheduledTask -TaskPath $folder | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-Table -AutoSize
