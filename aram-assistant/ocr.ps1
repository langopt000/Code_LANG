param([string]$Path)

# Windows built-in OCR (Windows.Media.Ocr). No third-party binaries required.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$ErrorActionPreference = 'Stop'

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        return $netTask.Result
    }

    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

    $langs = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
    if ($null -eq $langs -or $langs.Count -eq 0) {
        Write-Output '__OCR_UNAVAILABLE__'
        exit 2
    }

    # 卡牌字号偏小，放大 2 倍再识别可明显提升中文命中率；失败则退回原图。
    $upscaled = $null
    try {
        Add-Type -AssemblyName System.Drawing
        $srcImg = [System.Drawing.Image]::FromFile($Path)
        $nw = [int]($srcImg.Width * 2); $nh = [int]($srcImg.Height * 2)
        $dstImg = New-Object System.Drawing.Bitmap($nw, $nh)
        $gfx = [System.Drawing.Graphics]::FromImage($dstImg)
        $gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $gfx.DrawImage($srcImg, 0, 0, $nw, $nh)
        $gfx.Dispose(); $srcImg.Dispose()
        $upscaled = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), 'aram_ocr_2x.png')
        $dstImg.Save($upscaled, [System.Drawing.Imaging.ImageFormat]::Png)
        $dstImg.Dispose()
        $Path = $upscaled
    } catch {
        if ($upscaled) { Remove-Item $upscaled -ErrorAction SilentlyContinue }
        $upscaled = $null
    }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    # 国服客户端是中文卡牌名，优先用中文识别器；没有中文包再退回系统语言。
    $zh = $langs | Where-Object { $_.LanguageTag -like 'zh*' } | Select-Object -First 1
    if ($null -ne $zh) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($zh)
    }
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    }
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($langs[0])
    }
    if ($null -eq $engine) {
        Write-Output '__OCR_UNAVAILABLE__'
        exit 2
    }

    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) { Write-Output $line.Text }

    $stream.Dispose()
    if ($upscaled) { Remove-Item $upscaled -ErrorAction SilentlyContinue }
} catch {
    Write-Output ('__OCR_ERROR__' + $_.Exception.Message)
    exit 3
}
