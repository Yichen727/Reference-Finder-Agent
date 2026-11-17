param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$inputPath = (Resolve-Path -LiteralPath $Path).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$powerpoint = $null
$presentation = $null
$originalPresentationCount = 0
try {
    $powerpoint = New-Object -ComObject PowerPoint.Application
    $originalPresentationCount = $powerpoint.Presentations.Count
    # Read-only, no presentation window. Do not save over the exported file.
    $presentation = $powerpoint.Presentations.Open($inputPath, -1, 0, 0)
    $slides = @()
    foreach ($slide in $presentation.Slides) {
        $shapes = @()
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
                $range = $shape.TextFrame.TextRange
                if ($shape.Name -eq 'TITLE') {
                    if ($range.Font.Size -gt 54 -or $range.Font.Size -lt 18) {
                        throw ('Title size must stay between 18pt and 54pt on slide {0}' -f $slide.SlideIndex)
                    }
                    if ($range.BoundHeight -gt $range.Font.Size * 1.4) {
                        throw ('Title must remain on one line on slide {0}' -f $slide.SlideIndex)
                    }
                }
                $availableHeight = $shape.Height - $shape.TextFrame.MarginTop - $shape.TextFrame.MarginBottom
                $availableWidth = $shape.Width - $shape.TextFrame.MarginLeft - $shape.TextFrame.MarginRight
                if ($shape.Name -in @('TITLE', 'BODY COPY', 'SECTION')) {
                    if ($range.BoundHeight -gt $availableHeight + 1 -or $range.BoundWidth -gt $availableWidth + 1) {
                        throw ('Text exceeds its frame on slide {0}: {1}' -f $slide.SlideIndex, $shape.Name)
                    }
                }
                $shapes += [pscustomobject]@{
                    name = $shape.Name
                    text = $range.Text
                    font = $range.Font.Name
                    fontSize = $range.Font.Size
                    boundHeight = $range.BoundHeight
                    availableHeight = $availableHeight
                    boundWidth = $range.BoundWidth
                    availableWidth = $availableWidth
                }
            }
        }
        $slide.Export((Join-Path $outputPath ('slide-{0:D3}.png' -f $slide.SlideIndex)), 'PNG', 1600, 900)
        $links = @($slide.Hyperlinks | ForEach-Object { $_.Address })
        $slides += [pscustomobject]@{ index = $slide.SlideIndex; shapes = $shapes; hyperlinks = $links }
    }
    $report = [pscustomobject]@{
        file = $inputPath
        slideCount = $presentation.Slides.Count
        slides = $slides
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputPath 'powerpoint-report.json') -Encoding UTF8
    Write-Output ('PowerPoint opened and rendered {0} slides without saving changes. Report: {1}' -f $presentation.Slides.Count, $outputPath)
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    }
    if ($null -ne $powerpoint) {
        # Leave any presentations already open in the user's PowerPoint untouched.
        if ($originalPresentationCount -eq 0 -and $powerpoint.Presentations.Count -eq 0) {
            $powerpoint.Quit()
        }
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($powerpoint)
    }
}
