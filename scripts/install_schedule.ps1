# Registers the yt-autopilot tasks in Windows Task Scheduler (folder \yt-autopilot\).
# Runs as the current user, only while logged on (Claude Code and the YouTube token live in this profile).
# StartWhenAvailable: a run missed because the PC was off/asleep starts as soon as it is back.
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

$tasks = @(
    @{ Name = 'generate-short'; Args = 'generate --type short'; Trigger = New-ScheduledTaskTrigger -Daily -At '09:00' },
    @{ Name = 'generate-long';  Args = 'generate --type long';  Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Thursday -At '09:00' },
    @{ Name = 'produce-1400';   Args = 'produce';               Trigger = New-ScheduledTaskTrigger -Daily -At '14:00' },
    @{ Name = 'produce-2100';   Args = 'produce';               Trigger = New-ScheduledTaskTrigger -Daily -At '21:00' },
    @{ Name = 'weekly-stats';   Args = 'stats';                 Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '20:00' }
)

foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute $cmd -Argument $t.Args -WorkingDirectory $project
    Register-ScheduledTask -TaskPath $folder -TaskName $t.Name -Action $action -Trigger $t.Trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output ("Registered {0,-15} -> task.cmd {1}" -f $t.Name, $t.Args)
}
Get-ScheduledTask -TaskPath $folder | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-Table -AutoSize
