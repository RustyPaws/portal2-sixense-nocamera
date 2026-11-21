# Contact sheet of screenshots (used by hand_test.py): sheet.ps1 <out.jpg> "<a.jpg>|<b.jpg>|..."
param([string]$out, [string]$list)
$files = $list.Split('|')
Add-Type -AssemblyName System.Drawing
$w = 512; $h = 320; $cols = 2
$rows = [math]::Ceiling($files.Count / $cols)
$bmp = New-Object System.Drawing.Bitmap ($w * $cols), ($h * $rows)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$font = New-Object System.Drawing.Font "Arial", 16
for ($i = 0; $i -lt $files.Count; $i++) {
  $img = [System.Drawing.Image]::FromFile($files[$i])
  $x = ($i % $cols) * $w; $y = [math]::Floor($i / $cols) * $h
  $g.DrawImage($img, $x, $y, $w, $h)
  $g.DrawString("$i", $font, [System.Drawing.Brushes]::Yellow, $x + 4, $y + 4)
  $img.Dispose()
}
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Jpeg)
