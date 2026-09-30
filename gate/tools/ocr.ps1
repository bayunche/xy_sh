# 截图 OCR（Windows 自带 WinRT OCR，中文系统原生支持 zh-CN）
# 用法: powershell -File ocr.ps1 <png路径> [lang]
# 输出: JSON 数组写到 <png路径>.ocr.json（utf-8；stdout 编码不可靠必须走文件），
#       元素 = {text, x, y, w, h}（整行包围盒，取行内词的并集）
param([string]$Image, [string]$Lang = "zh-CN")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$path = (Resolve-Path $Image).Path
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($Lang))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$out = @()
foreach ($line in $result.Lines) {
    $words = @($line.Words)
    if ($words.Count -eq 0) { continue }
    $x1 = [double]::MaxValue; $y1 = [double]::MaxValue
    $x2 = [double]::MinValue; $y2 = [double]::MinValue
    foreach ($w in $words) {
        $r = $w.BoundingRect
        if ($r.X -lt $x1) { $x1 = $r.X }
        if ($r.Y -lt $y1) { $y1 = $r.Y }
        if (($r.X + $r.Width) -gt $x2) { $x2 = $r.X + $r.Width }
        if (($r.Y + $r.Height) -gt $y2) { $y2 = $r.Y + $r.Height }
    }
    $out += [pscustomobject]@{ text = $line.Text
        x = [int]$x1; y = [int]$y1; w = [int]($x2 - $x1); h = [int]($y2 - $y1) }
}
$json = $out | ConvertTo-Json -Compress
if ($out.Count -eq 1) { $json = "[$json]" }   # 单元素时 PS 不输出数组括号
[System.IO.File]::WriteAllText("$path.ocr.json", $json, (New-Object System.Text.UTF8Encoding($false)))
