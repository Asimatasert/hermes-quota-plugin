<#
  Installs the Hermes Agent Bar for the current user.
    - copies AgentBar.ps1 + start-hidden.vbs to C:\Tools\HermesHost\AgentBar (or -Target)
    - registers the scheduled task "Hermes_AgentBar": at logon of the current user, run level Limited
    - starts it now (unless -NoStart)
  Nothing else is installed or changed. Uninstall:  install.ps1 -Uninstall
#>
param(
    [string]$Target = 'C:\Tools\HermesHost\AgentBar',
    [string]$TaskName = 'Hermes_AgentBar',
    [switch]$NoStart,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function Stop-AgentBar {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*AgentBar.ps1*' -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Stop-AgentBar
    Write-Host "Removed task $TaskName. Files left in $Target (delete the folder by hand if wanted)."
    return
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
Stop-AgentBar
foreach ($f in 'AgentBar.ps1', 'start-hidden.vbs') { Copy-Item -Path (Join-Path $src $f) -Destination (Join-Path $Target $f) -Force }

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f (Join-Path $Target 'start-hidden.vbs')) -WorkingDirectory $Target
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Hermes Agent Bar (fake notch with quota, RAM/CPU and running agent jobs).' -Force | Out-Null
Write-Host "Installed to $Target, task $TaskName registered (at logon, Limited)."

if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host 'Started.'
}
