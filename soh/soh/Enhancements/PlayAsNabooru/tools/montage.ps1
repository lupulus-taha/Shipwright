# Tiles test screenshots into one labelled sheet: crops a box around her from each shot.
#   powershell -File montage.ps1 -Dir <run output> -Out sheet.png [-Filter "Slash_*"] [-Cols 8] [-Box 300] [-CenterX 480 -CenterY 470]
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Filter = "*.png",
    [int]$Cols = 8,
    [int]$Box = 300,
    [int]$CenterX = 480,
    [int]$CenterY = 470,
    [double]$Scale = 1.0
)
Add-Type -AssemblyName System.Drawing
$files = Get-ChildItem $Dir -Filter $Filter | Where-Object { $_.FullName -ne (Resolve-Path -LiteralPath $Out -ErrorAction SilentlyContinue).Path } | Sort-Object Name
if (-not $files) { Write-Host "no files"; exit 1 }
$cell = [int]($Box * $Scale)
$label = 18
$rows = [math]::Ceiling($files.Count / $Cols)
$sheet = New-Object System.Drawing.Bitmap ($Cols * $cell), ($rows * ($cell + $label))
$g = [System.Drawing.Graphics]::FromImage($sheet)
$g.Clear([System.Drawing.Color]::White)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$font = New-Object System.Drawing.Font "Segoe UI", 10
$src = New-Object System.Drawing.Rectangle ($CenterX - $Box / 2), ($CenterY - $Box / 2), $Box, $Box
for ($i = 0; $i -lt $files.Count; $i++) {
    $img = [System.Drawing.Image]::FromFile($files[$i].FullName)
    $x = ($i % $Cols) * $cell
    $y = [math]::Floor($i / $Cols) * ($cell + $label)
    $g.DrawImage($img, (New-Object System.Drawing.Rectangle $x, ($y + $label), $cell, $cell), $src, [System.Drawing.GraphicsUnit]::Pixel)
    $g.DrawString($files[$i].BaseName, $font, [System.Drawing.Brushes]::Black, $x + 2, $y)
    $img.Dispose()
}
$sheet.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "wrote $Out ($($files.Count) tiles)"
