param(
    [Parameter(Mandatory=$true)][string]$StarUmlSource,
    [string]$VisualParadigmSource
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gitExe = (Get-Command git -ErrorAction Stop).Source
$pythonExe = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Install Python with pythonw.exe first.' }
if (git -C $repoRoot status --porcelain) { throw 'Commit or resolve local changes before setup.' }
$sources = @(@{name='StarUML';source=(Resolve-Path -LiteralPath $StarUmlSource).Path;destination='models/machine-vision/Machine VIsion.mdj'})
if ($VisualParadigmSource) {
    $sources += @{name='Visual Paradigm';source=(Resolve-Path -LiteralPath $VisualParadigmSource).Path;destination='models/machine-vision/Machine Vision Lab.vpp'}
}
# Ask Git Credential Manager for the authenticated GitHub identity. Never log credentials.
$credentialLines = "protocol=https`nhost=github.com`n`n" | & $gitExe credential fill
if ($LASTEXITCODE -ne 0) { throw 'Sign in to GitHub using Git Credential Manager first.' }
$credentials = @{}
foreach ($line in $credentialLines) {
    $parts = $line -split '=',2
    if ($parts.Length -eq 2) { $credentials[$parts[0]] = $parts[1] }
}
$headers = @{Authorization="Bearer $($credentials['password'])";Accept='application/vnd.github+json';'User-Agent'='Integration2'}
$account = Invoke-RestMethod 'https://api.github.com/user' -Headers $headers
$repository = Invoke-RestMethod 'https://api.github.com/repos/Kirichovka/integration2' -Headers $headers
if (-not $repository.permissions.push) { throw 'Ask the repository owner for collaborator write access first.' }
$login = $account.login
if ($login -notmatch '^[A-Za-z0-9-]+$') { throw 'Unexpected GitHub login format.' }
$branch = "contributors/$login"
$taskName = 'Integration2 Model Save Watcher'
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) { Stop-ScheduledTask -TaskName $taskName }
git -C $repoRoot fetch origin
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $repoRoot show-ref --verify --quiet "refs/heads/$branch"
if ($LASTEXITCODE -eq 0) {
    git -C $repoRoot switch $branch
} else {
    git -C $repoRoot show-ref --verify --quiet "refs/remotes/origin/$branch"
    if ($LASTEXITCODE -eq 0) { git -C $repoRoot switch --track "origin/$branch" }
    else { git -C $repoRoot switch -c $branch origin/main }
}
if ($LASTEXITCODE -ne 0) { throw 'Cannot switch to contributor branch.' }
if (-not (git -C $repoRoot config user.name)) { git -C $repoRoot config user.name $login }
if (-not (git -C $repoRoot config user.email)) { git -C $repoRoot config user.email "$($account.id)+$login@users.noreply.github.com" }
git -C $repoRoot push -u origin $branch
if ($LASTEXITCODE -ne 0) { throw 'Cannot push contributor branch. Resolve divergence without force pushing.' }
$stateDir = Join-Path $repoRoot '.git/model-watcher'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
@{repository=$repoRoot;git=$gitExe;branch=$branch;push=$true;update_manifest=$false;receive_main=$true;
  poll_seconds=2;settle_seconds=6;models=$sources} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$stateDir/config.json" -Encoding utf8
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '"{0}" --config "{1}"' -f (Join-Path $repoRoot 'tools/watch_models.py'), "$stateDir/config.json"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Publish model saves to the authenticated contributor branch.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Ready: $login -> $branch. Saves are published automatically; main updates apply when editors are closed."
