param([int]$BackendPort = 8000, [int]$FrontendPort = 8501)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$processes = @(Get-CimInstance Win32_Process | ForEach-Object {
    if ($_.ExecutablePath -and $_.CommandLine) {
        [PSCustomObject]@{
            pid = [int]$_.ProcessId
            ppid = [int]$_.ParentProcessId
            created = $_.CreationDate.ToFileTimeUtc()
            exe = $_.ExecutablePath
            command = $_.CommandLine
        }
    }
})
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object {
    $_.LocalPort -in $BackendPort, $FrontendPort
} | ForEach-Object {
    [PSCustomObject]@{ port = [int]$_.LocalPort; pid = [int]$_.OwningProcess }
})
@{ processes = $processes; listeners = $listeners } | ConvertTo-Json -Depth 4 -Compress
