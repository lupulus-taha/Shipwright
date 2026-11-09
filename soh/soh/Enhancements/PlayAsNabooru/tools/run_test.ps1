# Runs a Play as Nabooru test script (see ../TestHarness.cpp) in an isolated SoH home folder, so the player's own
# saves and settings are never touched, and captures the screenshots the script asks for.
#
#   powershell -File run_test.ps1 -Script moves.txt -Out C:\temp\run1 [-Entrance 205] [-Mods <folder with .o2r>]
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$Entrance = 0xCD, # Hyrule Field
    [string]$TestHome = "$env:USERPROFILE\oot-fast64\soh_test",
    [string]$Exe = "",
    [string]$Mods = "",
    [int]$Width = 960,
    [int]$Height = 720,
    [int]$TimeoutSeconds = 300
)
$ErrorActionPreference = "Stop"
if (-not $Exe) { $Exe = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..\..\..\..\x64\Debug\soh.exe" }
$Exe = (Resolve-Path $Exe).Path
$exeDir = Split-Path $Exe
if (-not $Mods) { $Mods = Join-Path $exeDir "mods" }

# --- isolated home: archives are hard links to the real ones, the config is written fresh every run
New-Item -ItemType Directory -Force "$TestHome\mods", $Out | Out-Null
foreach ($archive in "oot.o2r", "soh.o2r") {
    $target = Join-Path $TestHome $archive
    if (-not (Test-Path $target)) { New-Item -ItemType HardLink -Path $target -Target (Join-Path $exeDir $archive) | Out-Null }
}
Remove-Item "$TestHome\mods\*" -Force -ErrorAction SilentlyContinue
Copy-Item "$Mods\*.o2r" "$TestHome\mods\"
Remove-Item "$TestHome\Save" -Recurse -Force -ErrorAction SilentlyContinue

$config = [ordered]@{
    ConfigVersion = 7
    CVars         = @{
        gSettings     = @{ BootSequence = 4; Volume = @{ Master = 0 }; OpenMenuBar = 0 }
        gEnhancements = @{ PlayAsNabooru = 1; DisableLOD = 1 }
    }
    Window        = @{
        AudioBackend = "wasapi"; Backend = @{ Id = 1; Name = "DirectX 11" }; Fullscreen = @{ Enabled = $false }
        Width = $Width; Height = $Height; PositionX = 40; PositionY = 40
    }
    WarpPoints    = @{
        test = @{ entranceId = $Entrance; roomNum = 0; pos = @{ x = 0; y = 0; z = 0 }; rotY = 0; bootToPoint = $true }
    }
}
$config | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 "$TestHome\shipofharkinian.json"

Remove-Item "$Out\*" -Force -Recurse -ErrorAction SilentlyContinue

Add-Type -ReferencedAssemblies System.Drawing @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class WindowShot {
    public delegate bool EnumProc(IntPtr hwnd, IntPtr lParam);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc proc, IntPtr lParam);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, System.Text.StringBuilder text, int max);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
    // the game window (a debug build also owns a console window)
    public static IntPtr Find(int processId) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            uint pid; GetWindowThreadProcessId(h, out pid);
            var title = new System.Text.StringBuilder(256); GetWindowText(h, title, 256);
            if (pid == processId && IsWindowVisible(h) && title.ToString().StartsWith("Ship of Harkinian")) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr hwnd, ref POINT point);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    public static void Save(IntPtr hwnd, string path) {
        SetProcessDPIAware(); // real pixel sizes on scaled displays
        RECT w; GetWindowRect(hwnd, out w);
        RECT c; GetClientRect(hwnd, out c);
        POINT origin = new POINT(); ClientToScreen(hwnd, ref origin);
        using (var full = new Bitmap(w.Right - w.Left, w.Bottom - w.Top)) {
            using (var g = Graphics.FromImage(full)) {
                IntPtr hdc = g.GetHdc();
                PrintWindow(hwnd, hdc, 2); // PW_RENDERFULLCONTENT: works for DirectX windows, even when covered
                g.ReleaseHdc(hdc);
            }
            var client = new Rectangle(origin.X - w.Left, origin.Y - w.Top, c.Right, c.Bottom);
            using (var crop = full.Clone(client, full.PixelFormat)) { crop.Save(path, ImageFormat.Png); }
        }
    }
}
"@

$env:NABOORU_TEST_SCRIPT = (Resolve-Path $Script).Path
$env:NABOORU_TEST_OUT = $Out
$proc = Start-Process -FilePath $Exe -WorkingDirectory $TestHome -PassThru
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$shots = 0
try {
    while (-not $proc.HasExited) {
        if ((Get-Date) -gt $deadline) { Write-Host "timeout"; break }
        $request = Join-Path $Out "shot.req"
        if (Test-Path $request) {
            $name = (Get-Content $request -Raw).Trim()
            Start-Sleep -Milliseconds 120 # let the paused frame present
            [WindowShot]::Save([WindowShot]::Find($proc.Id), (Join-Path $Out "$name.png"))
            Remove-Item $request -Force
            $shots++
        } else {
            Start-Sleep -Milliseconds 15
        }
    }
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
Write-Host "finished: $shots screenshots, done marker: $(Test-Path (Join-Path $Out 'done'))"
