<#
  Hermes Agent Bar for Windows - a fake "notch" at the top centre of the primary screen.

  Built only on what ships with Windows: Windows PowerShell 5.1 + WPF. No modules, no runtimes.
  Data comes from the Hermes CLI (quota plugin):
    hermes quota status --json --cached       quota rings          (every 30 s)
    hermes quota monitor --json snapshot      running jobs         (every 30 s, staggered by 15 s)
    hermes quota refresh                      cache refresh        (at most every 15 min, background)
  RAM / CPU come from kernel32 (GlobalMemoryStatusEx / GetSystemTimes) every 10 s.
  Every CLI call runs as an async child process; the UI thread never waits on it.

  Usage:  powershell -NoProfile -STA -WindowStyle Hidden -File AgentBar.ps1 [-Pinned] [-HermesPath <hermes.exe>]
          -Pinned keeps the card expanded (screenshots / debugging).
#>
param(
    [switch]$Pinned,
    [string]$HermesPath = '',
    [string]$WslDistro = 'hermes-dev',
    [string]$WslUser = 'dev'
)

$ErrorActionPreference = 'Stop'

# ---- single instance -------------------------------------------------------------------------
$script:Mutex = New-Object System.Threading.Mutex($false, 'Local\HermesAgentBar')
if (-not $script:Mutex.WaitOne(0)) { exit 0 }

$script:Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LogFile = Join-Path $script:Dir 'agentbar.log'
function Write-Log([string]$msg) {
    try {
        if ((Test-Path $script:LogFile) -and (Get-Item $script:LogFile).Length -gt 256KB) { Remove-Item $script:LogFile -Force }
        Add-Content -Path $script:LogFile -Value ('{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $msg) -Encoding UTF8
    } catch { }
}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

if (-not ('AgentBarNative' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AgentBarNative {
    [StructLayout(LayoutKind.Sequential)] public struct FILETIME { public uint Low; public uint High; }
    [DllImport("kernel32.dll")] static extern bool GetSystemTimes(out FILETIME idle, out FILETIME kernel, out FILETIME user);
    [StructLayout(LayoutKind.Sequential)] public class MEMORYSTATUSEX {
        public uint dwLength = (uint)Marshal.SizeOf(typeof(MEMORYSTATUSEX));
        public uint dwMemoryLoad; public ulong ullTotalPhys; public ulong ullAvailPhys;
        public ulong ullTotalPageFile; public ulong ullAvailPageFile; public ulong ullTotalVirtual;
        public ulong ullAvailVirtual; public ulong ullAvailExtendedVirtual;
    }
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GlobalMemoryStatusEx([In, Out] MEMORYSTATUSEX m);
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
    [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
    [DllImport("psapi.dll")] public static extern bool EmptyWorkingSet(IntPtr h);

    static ulong prevIdle, prevTotal;
    static ulong U(FILETIME f) { return ((ulong)f.High << 32) | f.Low; }
    // Whole-machine CPU busy % since the previous call (-1 on the first call).
    public static double CpuPercent() {
        FILETIME i, k, u;
        if (!GetSystemTimes(out i, out k, out u)) return -1;
        ulong idle = U(i), total = U(k) + U(u);
        bool first = prevTotal == 0;
        ulong di = idle - prevIdle, dt = total - prevTotal;
        prevIdle = idle; prevTotal = total;
        if (first || dt == 0) return -1;
        return Math.Max(0.0, Math.Min(100.0, 100.0 * (dt - di) / dt));
    }
    // [totalBytes, availBytes]
    public static ulong[] Memory() {
        var m = new MEMORYSTATUSEX();
        if (!GlobalMemoryStatusEx(m)) return new ulong[] { 0, 0 };
        return new ulong[] { m.ullTotalPhys, m.ullAvailPhys };
    }
}
'@
}

# ---- palette (Apple system colours, same as the macOS Agent Bar) -------------------------------
function Brush([string]$hex) { $b = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString($hex)); $b.Freeze(); $b }
$C = @{
    Bg = Brush '#FF000000'; Card = Brush '#FF0E0E10'; Section = Brush '#FF18181B'; Track = Brush '#FF2C2C30'
    Text = Brush '#FFF2F2F7'; Sub = Brush '#FF8E8E93'; Green = Brush '#FF30D158'; Yellow = Brush '#FFFFD60A'
    Orange = Brush '#FFFF9F0A'; Red = Brush '#FFFF453A'; Blue = Brush '#FF0A84FF'; Grey = Brush '#FF636366'
}

# ---- state ----------------------------------------------------------------------------------
$script:S = @{
    Quota = $null; QuotaAt = $null; QuotaErr = $null
    Snap = $null; SnapAt = $null; SnapErr = $null
    Wsl = 'bilinmiyor'; WslContainers = $null
    Cpu = -1; TotalGB = 0; FreeGB = 0
    LastRefresh = [DateTime]::MinValue
    Expanded = $false; Tick = 0
}
$script:Pending = @{}

# ---- hermes executable ------------------------------------------------------------------------
function Resolve-Hermes {
    if ($HermesPath -and (Test-Path $HermesPath)) { return $HermesPath }
    $cmd = Get-Command hermes -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:LOCALAPPDATA 'hermes\bin\hermes.exe'
    if (Test-Path $fallback) { return $fallback }
    return $null
}
$script:Hermes = Resolve-Hermes
Write-Log ("start pid={0} hermes={1}" -f $PID, ($(if ($script:Hermes) { 'found' } else { 'missing' })))

# ---- async child processes ---------------------------------------------------------------------
function Start-Job2([string]$name, [string]$file, [string]$arguments, [int]$timeoutSec, [scriptblock]$onDone, [System.Text.Encoding]$enc) {
    if ($script:Pending.ContainsKey($name)) { return }
    if (-not $file) { & $onDone $null 'hermes bulunamadı'; return }
    if (-not $enc) { $enc = [System.Text.Encoding]::UTF8 }
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $file; $psi.Arguments = $arguments
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = $enc
        $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        $psi.EnvironmentVariables['NO_COLOR'] = '1'
        $p = [System.Diagnostics.Process]::Start($psi)
        $script:Pending[$name] = @{
            P = $p; Out = $p.StandardOutput.ReadToEndAsync(); Err = $p.StandardError.ReadToEndAsync()
            Deadline = [DateTime]::UtcNow.AddSeconds($timeoutSec); Done = $onDone
        }
        if (-not $script:PollTimer.IsEnabled) { $script:PollTimer.Start() }
    } catch {
        & $onDone $null ('başlatılamadı: ' + $_.Exception.Message)
    }
}

function Stop-Tree([System.Diagnostics.Process]$p) {
    try { Start-Process -FilePath 'taskkill.exe' -ArgumentList "/PID $($p.Id) /T /F" -WindowStyle Hidden } catch { }
}

function Poll-Jobs {
    foreach ($name in @($script:Pending.Keys)) {
        $j = $script:Pending[$name]
        if ($j.Out.IsCompleted -and $j.P.HasExited) {
            $script:Pending.Remove($name)
            $out = $j.Out.Result; $code = $j.P.ExitCode
            $j.P.Dispose()
            try { & $j.Done $out $(if ($code -eq 0) { $null } else { "çıkış kodu $code" }) } catch { Write-Log "$name handler: $($_.Exception.Message)" }
        } elseif ([DateTime]::UtcNow -gt $j.Deadline) {
            $script:Pending.Remove($name)
            Stop-Tree $j.P
            try { & $j.Done $null 'zaman aşımı' } catch { }
        }
    }
    if ($script:Pending.Count -eq 0) { $script:PollTimer.Stop() }
}

function Parse-Json([string]$text) {
    if (-not $text) { return $null }
    $a = $text.IndexOf('{'); $b = $text.LastIndexOf('}')
    if ($a -lt 0 -or $b -le $a) { return $null }
    return ($text.Substring($a, $b - $a + 1) | ConvertFrom-Json)
}

# ---- data collectors -----------------------------------------------------------------------
function Update-Quota {
    Start-Job2 'quota' $script:Hermes 'quota status --json --cached' 20 {
        param($out, $err)
        $q = $null
        if (-not $err) { try { $q = Parse-Json $out } catch { $err = 'JSON okunamadı' } }
        if ($q) {
            $script:S.Quota = $q; $script:S.QuotaAt = Get-Date; $script:S.QuotaErr = $null
            # cache older than 15 min -> background refresh, at most once per 15 min
            $age = 0; try { $age = [double]$q.age_s } catch { }
            if ($age -gt 900 -and ((Get-Date) - $script:S.LastRefresh).TotalMinutes -ge 15) { Start-Refresh }
        } else { $script:S.QuotaErr = $(if ($err) { $err } else { 'veri yok' }) }
        Render
    } $null
}

function Start-Refresh {
    $script:S.LastRefresh = Get-Date
    Start-Job2 'refresh' $script:Hermes 'quota refresh' 180 { param($out, $err) if ($err) { Write-Log "refresh: $err" } else { Update-Quota } } $null
}

function Update-Snapshot {
    Start-Job2 'snap' $script:Hermes 'quota monitor --json snapshot' 30 {
        param($out, $err)
        $s = $null
        if (-not $err) { try { $s = Parse-Json $out } catch { $err = 'JSON okunamadı' } }
        if ($s -and $s.live) { $script:S.Snap = $s; $script:S.SnapAt = Get-Date; $script:S.SnapErr = $null }
        else { $script:S.SnapErr = $(if ($err) { $err } else { 'veri yok' }) }
        Render
    } $null
}

function Update-Wsl {
    # 'wsl -l --running' never boots a stopped distro; docker is asked only when it already runs.
    Start-Job2 'wsl' 'wsl.exe' '-l --running -q' 3 {
        param($out, $err)
        $running = $false
        if ($out) { $running = @($out -split "`r?`n" | ForEach-Object { $_.Trim([char]0, ' ') } | Where-Object { $_ -eq $WslDistro }).Count -gt 0 }
        if (-not $running) {
            $script:S.Wsl = $(if ($err -eq 'zaman aşımı') { 'yanıt yok' } else { 'durdu' }); $script:S.WslContainers = $null; Render; return
        }
        $script:S.Wsl = 'çalışıyor'
        Start-Job2 'docker' 'wsl.exe' ("-d $WslDistro -u $WslUser -- docker ps -q") 3 {
            param($o, $e)
            if ($e) { $script:S.WslContainers = $null; if ($e -eq 'zaman aşımı') { $script:S.Wsl = 'çalışıyor · docker yanıt vermedi' } }
            else { $script:S.WslContainers = @($o -split "`r?`n" | Where-Object { $_.Trim() }).Count }
            Render
        } $null
    } ([System.Text.Encoding]::Unicode)
}

function Update-System {
    $m = [AgentBarNative]::Memory()
    $script:S.TotalGB = $m[0] / 1GB; $script:S.FreeGB = $m[1] / 1GB
    $script:S.Cpu = [AgentBarNative]::CpuPercent()
}

# ---- derived values --------------------------------------------------------------------------
function Get-Accounts {
    $list = @()
    $q = $script:S.Quota
    if (-not $q -or -not $q.providers) { return $list }
    foreach ($prop in $q.providers.PSObject.Properties) {
        if ($prop.Name -notlike 'anthropic*') { continue }
        $p = $prop.Value
        if (-not $p.windows) { continue }
        $week = $p.windows | Where-Object { $_.label -eq 'Current week' } | Select-Object -First 1
        $sess = $p.windows | Where-Object { $_.label -eq 'Current session' } | Select-Object -First 1
        if (-not $week -and -not $sess) { continue }
        $label = $prop.Name
        if ($label -match '^anthropic[:/._-](.+)$') { $label = $Matches[1] }
        if ($label.Length -gt 12) { $label = $label.Substring(0, 11) + '…' }
        $list += [pscustomobject]@{ Label = $label; Week = $week; Session = $sess }
    }
    return $list
}

function Get-WeekMax {
    $max = $null
    foreach ($a in (Get-Accounts)) { if ($a.Week -and ($max -eq $null -or [double]$a.Week.used_percent -gt $max)) { $max = [double]$a.Week.used_percent } }
    return $max
}

function Get-Running {
    $s = $script:S.Snap
    if (-not $s) { return $null }
    $rows = @($s.live.running | Where-Object { $_ })
    $working = 0
    try { $working = [int]$s.live.counts.working } catch { $working = @($rows | Where-Object { $_.working }).Count }
    return [pscustomobject]@{ Working = $working; Rows = $rows }
}

function Format-Left($resetAt) {
    if (-not $resetAt) { return '' }
    try { $t = [DateTimeOffset]::Parse([string]$resetAt) } catch { return '' }
    $d = $t - [DateTimeOffset]::Now
    if ($d.TotalSeconds -le 0) { return 'şimdi' }
    if ($d.TotalDays -ge 1) { return ('{0}g {1}s' -f [int][math]::Floor($d.TotalDays), $d.Hours) }
    if ($d.TotalHours -ge 1) { return ('{0}s {1}d' -f [int][math]::Floor($d.TotalHours), $d.Minutes) }
    return ('{0}d' -f [math]::Max(1, $d.Minutes))
}

function Ring-Brush([double]$pct) { if ($pct -lt 50) { $C.Green } elseif ($pct -lt 80) { $C.Yellow } else { $C.Red } }

# ---- WPF helpers ------------------------------------------------------------------------------
function New-Text([string]$t, [double]$size = 12, $brush = $null, [string]$weight = 'Normal') {
    $tb = New-Object System.Windows.Controls.TextBlock
    $tb.Text = $t; $tb.FontSize = $size; $tb.Foreground = $(if ($brush) { $brush } else { $C.Text })
    $tb.FontWeight = [System.Windows.FontWeights]::$weight
    $tb.TextTrimming = 'CharacterEllipsis'
    return $tb
}

function New-Ring([double]$pct, [double]$size, [double]$thick, [string]$center) {
    $g = New-Object System.Windows.Controls.Grid
    $g.Width = $size; $g.Height = $size
    $bgE = New-Object System.Windows.Shapes.Ellipse
    $bgE.Stroke = $C.Track; $bgE.StrokeThickness = $thick
    [void]$g.Children.Add($bgE)
    $pct = [math]::Max(0, [math]::Min(100, $pct))
    $brush = Ring-Brush $pct
    $r = ($size - $thick) / 2; $cx = $size / 2; $cy = $size / 2
    if ($pct -ge 99.9) {
        $full = New-Object System.Windows.Shapes.Ellipse
        $full.Stroke = $brush; $full.StrokeThickness = $thick
        [void]$g.Children.Add($full)
    } elseif ($pct -gt 0.4) {
        $angle = $pct / 100 * 2 * [math]::PI
        $start = New-Object System.Windows.Point ($cx, ($cy - $r))
        $end = New-Object System.Windows.Point (($cx + $r * [math]::Sin($angle)), ($cy - $r * [math]::Cos($angle)))
        $seg = New-Object System.Windows.Media.ArcSegment ($end, (New-Object System.Windows.Size ($r, $r)), 0, ($pct -gt 50), ([System.Windows.Media.SweepDirection]::Clockwise), $true)
        $fig = New-Object System.Windows.Media.PathFigure
        $fig.StartPoint = $start; [void]$fig.Segments.Add($seg)
        $geo = New-Object System.Windows.Media.PathGeometry; [void]$geo.Figures.Add($fig)
        $path = New-Object System.Windows.Shapes.Path
        $path.Data = $geo; $path.Stroke = $brush; $path.StrokeThickness = $thick
        $path.StrokeStartLineCap = 'Round'; $path.StrokeEndLineCap = 'Round'
        [void]$g.Children.Add($path)
    }
    $t = New-Text $center ([math]::Round($size * 0.24)) $C.Text 'SemiBold'
    $t.HorizontalAlignment = 'Center'; $t.VerticalAlignment = 'Center'
    [void]$g.Children.Add($t)
    return $g
}

function New-Section([string]$title) {
    $b = New-Object System.Windows.Controls.Border
    $b.Background = $C.Section; $b.CornerRadius = New-Object System.Windows.CornerRadius 12
    $b.Padding = New-Object System.Windows.Thickness (12, 9, 12, 10)
    $b.Margin = New-Object System.Windows.Thickness (0, 0, 0, 8)
    $sp = New-Object System.Windows.Controls.StackPanel
    $h = New-Text $title 10 $C.Sub 'SemiBold'
    $h.Margin = New-Object System.Windows.Thickness (0, 0, 0, 6)
    [void]$sp.Children.Add($h)
    $b.Child = $sp
    return @{ Border = $b; Panel = $sp }
}

function New-Row([string]$left, [string]$right, $rightBrush = $null) {
    $g = New-Object System.Windows.Controls.Grid
    $g.Margin = New-Object System.Windows.Thickness (0, 1, 0, 1)
    $c1 = New-Object System.Windows.Controls.ColumnDefinition; $c1.Width = New-Object System.Windows.GridLength (1, 'Star')
    $c2 = New-Object System.Windows.Controls.ColumnDefinition; $c2.Width = [System.Windows.GridLength]::Auto
    [void]$g.ColumnDefinitions.Add($c1); [void]$g.ColumnDefinitions.Add($c2)
    $l = New-Text $left 12.5 $C.Sub
    $r = New-Text $right 12.5 $(if ($rightBrush) { $rightBrush } else { $C.Text }) 'SemiBold'
    $r.Margin = New-Object System.Windows.Thickness (12, 0, 0, 0)
    [System.Windows.Controls.Grid]::SetColumn($r, 1)
    [void]$g.Children.Add($l); [void]$g.Children.Add($r)
    return $g
}

function New-Badge([string]$text, $brush) {
    $b = New-Object System.Windows.Controls.Border
    $b.CornerRadius = New-Object System.Windows.CornerRadius 6
    $b.Padding = New-Object System.Windows.Thickness (6, 1, 6, 2)
    $col = ([System.Windows.Media.SolidColorBrush]$brush).Color
    $bg = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.Color]::FromArgb(56, $col.R, $col.G, $col.B))
    $b.Background = $bg
    $t = New-Text $text 10.5 $brush 'SemiBold'
    $b.Child = $t
    return $b
}

# ---- window --------------------------------------------------------------------------------
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Hermes Agent Bar" WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        Topmost="True" ShowInTaskbar="False" ShowActivated="False" ResizeMode="NoResize"
        Width="440" Height="700" UseLayoutRounding="True" SnapsToDevicePixels="True"
        FontFamily="Segoe UI Variable Text, Segoe UI" TextOptions.TextFormattingMode="Display">
  <Grid>
    <Border x:Name="Card" HorizontalAlignment="Center" VerticalAlignment="Top" Width="200" Height="30"
            Background="#FF000000" CornerRadius="0,0,14,14" ClipToBounds="True" Cursor="Hand">
      <Grid>
        <StackPanel x:Name="Pill" Orientation="Horizontal" HorizontalAlignment="Center" VerticalAlignment="Top" Height="30">
          <Ellipse x:Name="Dot" Width="8" Height="8" Fill="#FF30D158" VerticalAlignment="Center" Margin="0,0,8,0"/>
          <TextBlock x:Name="PillText" Text="Hermes" Foreground="#FFF2F2F7" FontSize="12" FontWeight="SemiBold"
                     VerticalAlignment="Center" TextTrimming="CharacterEllipsis" MaxWidth="160"/>
        </StackPanel>
        <StackPanel x:Name="Body" Width="396" Margin="0,36,0,0" HorizontalAlignment="Center" VerticalAlignment="Top" Opacity="0"/>
      </Grid>
    </Border>
  </Grid>
</Window>
'@
$reader = New-Object System.Xml.XmlNodeReader $xaml
$script:Win = [Windows.Markup.XamlReader]::Load($reader)
$script:Card = $script:Win.FindName('Card')
$script:Dot = $script:Win.FindName('Dot')
$script:PillText = $script:Win.FindName('PillText')
$script:Body = $script:Win.FindName('Body')

$CollapsedW = 200; $CollapsedH = 30; $ExpandedW = 420

function Place-Window {
    $w = [System.Windows.SystemParameters]::PrimaryScreenWidth
    $script:Win.Left = [math]::Round(($w - $script:Win.Width) / 2)
    $script:Win.Top = 0
}

$script:Win.Add_SourceInitialized({
    try {
        $h = (New-Object System.Windows.Interop.WindowInteropHelper $script:Win).Handle
        $ex = [AgentBarNative]::GetWindowLong($h, -20)
        # WS_EX_TOOLWINDOW (no Alt-Tab) | WS_EX_NOACTIVATE (never steals focus)
        [void][AgentBarNative]::SetWindowLong($h, -20, ($ex -bor 0x80 -bor 0x08000000))
    } catch { Write-Log "exstyle: $($_.Exception.Message)" }
    Place-Window
})

# ---- animation -------------------------------------------------------------------------------
$script:Ease = New-Object System.Windows.Media.Animation.QuadraticEase
$script:Ease.EasingMode = 'EaseOut'
function Animate($target, $prop, [double]$to, [int]$ms) {
    $a = New-Object System.Windows.Media.Animation.DoubleAnimation
    $a.To = $to; $a.Duration = New-Object System.Windows.Duration ([TimeSpan]::FromMilliseconds($ms))
    $a.EasingFunction = $script:Ease
    [System.Windows.Media.Animation.Timeline]::SetDesiredFrameRate($a, 60)
    $target.BeginAnimation($prop, $a)
}

function Get-BodyHeight {
    $script:Body.Measure((New-Object System.Windows.Size (396, [double]::PositiveInfinity)))
    # DesiredSize already includes Body's 36 px top margin
    return [math]::Min(680, $script:Body.DesiredSize.Height + 14)
}

function Expand {
    $script:S.Expanded = $true
    Build-Body
    $script:Card.CornerRadius = New-Object System.Windows.CornerRadius (0, 0, 22, 22)
    $script:Card.Background = $C.Card
    Animate $script:Card ([System.Windows.FrameworkElement]::WidthProperty) $ExpandedW 220
    Animate $script:Card ([System.Windows.FrameworkElement]::HeightProperty) (Get-BodyHeight) 240
    Animate $script:Body ([System.Windows.UIElement]::OpacityProperty) 1 260
}

function Collapse {
    if ($Pinned) { return }
    $script:S.Expanded = $false
    Animate $script:Body ([System.Windows.UIElement]::OpacityProperty) 0 120
    Animate $script:Card ([System.Windows.FrameworkElement]::WidthProperty) $CollapsedW 200
    Animate $script:Card ([System.Windows.FrameworkElement]::HeightProperty) $CollapsedH 200
    $script:Card.CornerRadius = New-Object System.Windows.CornerRadius (0, 0, 14, 14)
    $script:Card.Background = $C.Bg
}

# pulsing dot (only while jobs run; low frame rate keeps the layered window cheap)
$script:Pulse = New-Object System.Windows.Media.Animation.DoubleAnimation (1.0, 0.3, (New-Object System.Windows.Duration ([TimeSpan]::FromMilliseconds(900))))
$script:Pulse.AutoReverse = $true
$script:Pulse.RepeatBehavior = [System.Windows.Media.Animation.RepeatBehavior]::Forever
[System.Windows.Media.Animation.Timeline]::SetDesiredFrameRate($script:Pulse, 12)
$script:Pulsing = $false
function Set-Pulse([bool]$on) {
    if ($on -eq $script:Pulsing) { return }
    $script:Pulsing = $on
    if ($on) { $script:Dot.BeginAnimation([System.Windows.UIElement]::OpacityProperty, $script:Pulse) }
    else { $script:Dot.BeginAnimation([System.Windows.UIElement]::OpacityProperty, $null); $script:Dot.Opacity = 1 }
}

# ---- rendering -------------------------------------------------------------------------------
function Render {
    try {
        $run = Get-Running
        $working = $(if ($run) { $run.Working } else { 0 })
        $week = Get-WeekMax
        $freePct = $(if ($script:S.TotalGB -gt 0) { 100 * $script:S.FreeGB / $script:S.TotalGB } else { 100 })
        $usedPct = 100 - $freePct

        if (($week -ne $null -and $week -ge 20) -or $freePct -lt 20) { $dot = $C.Red; $pulse = $false }
        elseif ($week -ne $null -and $week -ge 15) { $dot = $C.Orange; $pulse = $false }
        elseif ($working -gt 0) { $dot = $C.Blue; $pulse = $true }
        else { $dot = $C.Green; $pulse = $false }
        $script:Dot.Fill = $dot
        Set-Pulse $pulse

        if ($working -gt 0) { $script:PillText.Text = ('{0} iş · %{1:0} RAM' -f $working, $usedPct) }
        elseif (-not $run -and $script:S.SnapErr -and -not $script:S.Quota) { $script:PillText.Text = 'veri yok' }
        else { $script:PillText.Text = 'boşta' }

        if ($script:S.Expanded) {
            Build-Body
            $h = Get-BodyHeight
            if ([math]::Abs($script:Card.ActualHeight - $h) -gt 1) { Animate $script:Card ([System.Windows.FrameworkElement]::HeightProperty) $h 160 }
        }
    } catch { Write-Log "render: $($_.Exception.Message)" }
}

function Build-Body {
    $b = $script:Body
    $b.Children.Clear()

    # 1. Kota
    $sec = New-Section 'KOTA'
    $accounts = @(Get-Accounts)
    if ($accounts.Count -eq 0) {
        [void]$sec.Panel.Children.Add((New-Text $(if ($script:S.QuotaErr) { "veri yok · $($script:S.QuotaErr)" } else { 'veri yok' }) 12.5 $C.Sub))
    } else {
        $wrap = New-Object System.Windows.Controls.WrapPanel
        foreach ($a in $accounts) {
            $tile = New-Object System.Windows.Controls.StackPanel
            $tile.Orientation = 'Horizontal'; $tile.Margin = New-Object System.Windows.Thickness (0, 0, 18, 2)
            foreach ($pair in @(@($a.Week, 'hafta', 58.0), @($a.Session, '5 saat', 44.0))) {
                $wnd = $pair[0]
                if (-not $wnd) { continue }
                $pct = [double]$wnd.used_percent
                $col = New-Object System.Windows.Controls.StackPanel
                $col.Margin = New-Object System.Windows.Thickness (0, 0, 12, 0)
                $ring = New-Ring $pct $pair[2] $(if ($pair[2] -gt 50) { 6 } else { 5 }) ('%{0:0}' -f $pct)
                $ring.HorizontalAlignment = 'Center'
                if ($pair[2] -lt 50) { $ring.Margin = New-Object System.Windows.Thickness (0, 7, 0, 7) }
                [void]$col.Children.Add($ring)
                $l1 = New-Text ($(if ($pair[1] -eq 'hafta') { $a.Label } else { $pair[1] })) 11 $C.Text 'SemiBold'
                $l1.HorizontalAlignment = 'Center'; $l1.MaxWidth = 84; $l1.Margin = New-Object System.Windows.Thickness (0, 4, 0, 0)
                $sub = $(if ($pair[1] -eq 'hafta') { 'hafta' } else { '' })
                $left = Format-Left $wnd.reset_at
                $l2 = New-Text (($sub, $left | Where-Object { $_ }) -join ' · ') 10.5 $C.Sub
                $l2.HorizontalAlignment = 'Center'
                [void]$col.Children.Add($l1); [void]$col.Children.Add($l2)
                [void]$tile.Children.Add($col)
            }
            [void]$wrap.Children.Add($tile)
        }
        [void]$sec.Panel.Children.Add($wrap)
        if ($script:S.QuotaErr) { [void]$sec.Panel.Children.Add((New-Text "son veri gösteriliyor · $($script:S.QuotaErr)" 10.5 $C.Orange)) }
    }
    [void]$b.Children.Add($sec.Border)

    # 2. Windows
    $sec = New-Section 'WINDOWS'
    $total = $script:S.TotalGB; $free = $script:S.FreeGB
    $freePct = $(if ($total -gt 0) { 100 * $free / $total } else { 0 })
    $ramBrush = $(if ($freePct -lt 20) { $C.Red } elseif ($freePct -lt 35) { $C.Yellow } else { $C.Text })
    [void]$sec.Panel.Children.Add((New-Row 'RAM' ('{0:0.0} / {1:0.0} GB · %{2:0} boş' -f ($total - $free), $total, $freePct) $ramBrush))
    $cpuTxt = $(if ($script:S.Cpu -ge 0) { '%{0:0}' -f $script:S.Cpu } else { '…' })
    [void]$sec.Panel.Children.Add((New-Row 'CPU' $cpuTxt $(if ($script:S.Cpu -ge 85) { $C.Red } else { $null })))
    $run = Get-Running
    $working = $(if ($run) { $run.Working } else { 0 })
    $extra = [math]::Floor(($free - 0.20 * $total) / 3)
    if ($extra -lt 0) { $extra = 0 }
    $cap = $extra + $working
    [void]$sec.Panel.Children.Add((New-Row 'Kapasite' ('{0}/{1} iş' -f $working, $cap) $(if ($working -ge $cap) { $C.Orange } else { $C.Green })))
    [void]$b.Children.Add($sec.Border)

    # 3. İşler
    $sec = New-Section 'İŞLER'
    if (-not $run) {
        $n = @(Get-Process -Name hermes -ErrorAction SilentlyContinue).Count
        [void]$sec.Panel.Children.Add((New-Text ("veri yok · {0} hermes süreci" -f $n) 12.5 $C.Sub))
    } elseif ($run.Rows.Count -eq 0) {
        [void]$sec.Panel.Children.Add((New-Text 'aktif iş yok' 12.5 $C.Sub))
    } else {
        $shown = 0
        foreach ($r in $run.Rows) {
            if ($shown -ge 5) { break }
            $shown++
            $state = [string]$r.state
            switch ($state) {
                'working' { $txt = 'çalışıyor'; $br = $C.Blue }
                'needs_approval' { $txt = 'onay'; $br = $C.Orange }
                'your_turn' { $txt = 'sıra sende'; $br = $C.Yellow }
                'stalled' { $txt = 'takıldı'; $br = $C.Red }
                default { if ($r.working) { $txt = 'çalışıyor'; $br = $C.Blue } else { $txt = 'bekliyor'; $br = $C.Grey } }
            }
            $g = New-Object System.Windows.Controls.Grid
            $g.Margin = New-Object System.Windows.Thickness (0, 2, 0, 2)
            foreach ($w in @('Auto', 'Star', 'Auto')) {
                $cd = New-Object System.Windows.Controls.ColumnDefinition
                $cd.Width = $(if ($w -eq 'Star') { New-Object System.Windows.GridLength (1, 'Star') } else { [System.Windows.GridLength]::Auto })
                [void]$g.ColumnDefinitions.Add($cd)
            }
            $badge = New-Badge $txt $br; $badge.VerticalAlignment = 'Center'
            $title = [string]$r.title; if (-not $title) { $title = '(adsız oturum)' }
            $t = New-Text $title 12.5 $C.Text; $t.Margin = New-Object System.Windows.Thickness (8, 0, 8, 0); $t.VerticalAlignment = 'Center'
            $meta = @($r.profile, $(if ($r.subagent) { 'alt ajan' })) | Where-Object { $_ }
            $m = New-Text ($meta -join ' · ') 11 $C.Sub; $m.VerticalAlignment = 'Center'
            [System.Windows.Controls.Grid]::SetColumn($t, 1); [System.Windows.Controls.Grid]::SetColumn($m, 2)
            [void]$g.Children.Add($badge); [void]$g.Children.Add($t); [void]$g.Children.Add($m)
            [void]$sec.Panel.Children.Add($g)
        }
        if ($run.Rows.Count -gt 5) { [void]$sec.Panel.Children.Add((New-Text ('+{0} oturum daha' -f ($run.Rows.Count - 5)) 11 $C.Sub)) }
    }
    [void]$b.Children.Add($sec.Border)

    # 4. WSL
    $sec = New-Section "WSL · $WslDistro"
    $wslTxt = $script:S.Wsl
    if ($script:S.WslContainers -ne $null) { $wslTxt = '{0} · {1} konteyner' -f $script:S.Wsl, $script:S.WslContainers }
    $wslBrush = $(if ($script:S.Wsl -like 'çalışıyor*') { $C.Green } elseif ($script:S.Wsl -eq 'durdu') { $C.Sub } else { $C.Orange })
    [void]$sec.Panel.Children.Add((New-Row 'Durum' $wslTxt $wslBrush))
    [void]$b.Children.Add($sec.Border)

    $stamp = $script:S.QuotaAt; if ($script:S.SnapAt -and (-not $stamp -or $script:S.SnapAt -gt $stamp)) { $stamp = $script:S.SnapAt }
    $foot = New-Text $(if ($stamp) { 'güncellendi {0:HH:mm:ss}' -f $stamp } else { 'veri bekleniyor…' }) 10 $C.Grey
    $foot.HorizontalAlignment = 'Center'; $foot.Margin = New-Object System.Windows.Thickness (0, 0, 0, 2)
    [void]$b.Children.Add($foot)
}

# ---- interaction -----------------------------------------------------------------------------
$script:CollapseTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:CollapseTimer.Interval = [TimeSpan]::FromMilliseconds(1500)
$script:CollapseTimer.Add_Tick({ $script:CollapseTimer.Stop(); Collapse })

$script:Card.Add_MouseEnter({ $script:CollapseTimer.Stop(); if (-not $script:S.Expanded) { Expand } })
$script:Card.Add_MouseLeave({ if (-not $Pinned) { $script:CollapseTimer.Stop(); $script:CollapseTimer.Start() } })
$script:Card.Add_MouseLeftButtonUp({ if (-not $script:S.Expanded) { Expand } })

$menu = New-Object System.Windows.Controls.ContextMenu
$miRefresh = New-Object System.Windows.Controls.MenuItem; $miRefresh.Header = 'Yenile'
$miClose = New-Object System.Windows.Controls.MenuItem; $miClose.Header = 'Kapat'
[void]$menu.Items.Add($miRefresh); [void]$menu.Items.Add($miClose)
$script:Card.ContextMenu = $menu
$miRefresh.Add_Click({
    Update-System; Update-Quota; Update-Snapshot; Update-Wsl
    if (((Get-Date) - $script:S.LastRefresh).TotalMinutes -ge 15) { Start-Refresh }
    Render
})
$miClose.Add_Click({ $script:Win.Close() })

# ---- timers ---------------------------------------------------------------------------------
$script:PollTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:PollTimer.Interval = [TimeSpan]::FromMilliseconds(500)
$script:PollTimer.Add_Tick({ try { Poll-Jobs } catch { Write-Log "poll: $($_.Exception.Message)" } })

# 10 s: RAM / CPU; every 3rd tick (30 s): WSL
$script:SysTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:SysTimer.Interval = [TimeSpan]::FromSeconds(10)
$script:SysTimer.Add_Tick({
    try {
        $script:S.Tick++
        Place-Window   # follows resolution / primary-monitor changes
        Update-System
        if ($script:S.Tick % 3 -eq 0) { Update-Wsl }
        Render
    } catch { Write-Log "sys: $($_.Exception.Message)" }
})

# 15 s, alternating: quota (30 s) / snapshot (30 s) -> never two hermes processes at once
$script:DataFlip = $false
$script:DataTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:DataTimer.Interval = [TimeSpan]::FromSeconds(15)
$script:DataTimer.Add_Tick({
    try {
        $script:DataFlip = -not $script:DataFlip
        if ($script:DataFlip) { Update-Snapshot } else { Update-Quota }
    } catch { Write-Log "data: $($_.Exception.Message)" }
})

# trim the start-up working set once (WPF + Add-Type compile garbage)
$script:TrimTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:TrimTimer.Interval = [TimeSpan]::FromSeconds(20)
$script:TrimTimer.Add_Tick({
    $script:TrimTimer.Stop()
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    try { [void][AgentBarNative]::EmptyWorkingSet((Get-Process -Id $PID).Handle) } catch { }
})

$script:Win.Add_Loaded({
    Update-System; [void][AgentBarNative]::CpuPercent()
    Render
    Update-Quota; Update-Wsl
    $script:SysTimer.Start(); $script:DataTimer.Start(); $script:TrimTimer.Start()
    if ($Pinned) { Expand }
})
$script:Win.Add_Closed({
    foreach ($j in @($script:Pending.Values)) { Stop-Tree $j.P }
    Write-Log 'stop'
})

try {
    [void]$script:Win.ShowDialog()
} catch {
    Write-Log "fatal: $($_.Exception.Message)"
} finally {
    $script:Mutex.ReleaseMutex()
}
