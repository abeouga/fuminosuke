#Requires -Version 5.1

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$projectRoot = 'C:\Users\hanam\Desktop\llm-ocr'
$startBat = Join-Path $projectRoot 'start.bat'
$scanFolder = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LLM-OCR Scans'
$epsonScan = 'C:\Program Files (x86)\epson\Epson Scan 2\Core\es2launcher.exe'
$printerName = 'EPSON EW-M530F Series'
$scannerAddress = '192.168.223.1'

function Ensure-ScanFolder {
    if (-not (Test-Path -LiteralPath $scanFolder)) {
        New-Item -ItemType Directory -Path $scanFolder -Force | Out-Null
    }
}

function Test-LocalPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return $result.AsyncWaitHandle.WaitOne(150) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Find-FrontendUrl {
    foreach ($port in 5173..5193) {
        if (Test-LocalPort $port) {
            return "http://127.0.0.1:$port"
        }
    }
    return $null
}

function Show-LauncherError([string]$Title, [System.Exception]$Error) {
    [System.Windows.Forms.MessageBox]::Show(
        $Error.Message,
        $Title,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function New-LauncherButton([string]$Text, [int]$Top, [scriptblock]$Action, [bool]$Primary = $false) {
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.Left = 28
    $button.Top = $Top
    $button.Width = 332
    $button.Height = 42
    $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $button.FlatAppearance.BorderSize = 1
    $button.Font = New-Object System.Drawing.Font('Yu Gothic UI', 10, [System.Drawing.FontStyle]::Bold)
    if ($Primary) {
        $button.BackColor = [System.Drawing.Color]::FromArgb(24, 91, 82)
        $button.ForeColor = [System.Drawing.Color]::White
        $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(24, 91, 82)
    }
    else {
        $button.BackColor = [System.Drawing.Color]::White
        $button.ForeColor = [System.Drawing.Color]::FromArgb(35, 45, 55)
        $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(170, 182, 190)
    }
    $button.Add_Click($Action)
    return $button
}

Ensure-ScanFolder

$form = New-Object System.Windows.Forms.Form
$form.Text = 'LLM-OCR 開発ランチャー'
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.ClientSize = New-Object System.Drawing.Size(388, 520)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(239, 242, 241)
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi
$form.Font = New-Object System.Drawing.Font('Yu Gothic UI', 9)

$header = New-Object System.Windows.Forms.Panel
$header.Dock = [System.Windows.Forms.DockStyle]::Top
$header.Height = 86
$header.BackColor = [System.Drawing.Color]::FromArgb(28, 42, 49)
$form.Controls.Add($header)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'LLM-OCR  開発ツール'
$title.Left = 24
$title.Top = 16
$title.Width = 330
$title.Height = 28
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font('Yu Gothic UI', 15, [System.Drawing.FontStyle]::Bold)
$header.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = 'EPSON EW-M530F / ローカル開発環境'
$subtitle.Left = 25
$subtitle.Top = 49
$subtitle.Width = 330
$subtitle.Height = 22
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(184, 207, 201)
$subtitle.Font = New-Object System.Drawing.Font('Yu Gothic UI', 9)
$header.Controls.Add($subtitle)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = '待機中'
$statusLabel.Left = 28
$statusLabel.Top = 454
$statusLabel.Width = 332
$statusLabel.Height = 38
$statusLabel.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
$statusLabel.BackColor = [System.Drawing.Color]::White
$statusLabel.ForeColor = [System.Drawing.Color]::FromArgb(65, 77, 84)
$statusLabel.Padding = New-Object System.Windows.Forms.Padding(10, 8, 10, 8)
$form.Controls.Add($statusLabel)

$startButton = New-LauncherButton '1. システムを起動' 108 {
    try {
        if (-not (Test-Path -LiteralPath $startBat -PathType Leaf)) {
            throw "start.bat が見つかりません: $startBat"
        }
        $url = Find-FrontendUrl
        if ($url) {
            Start-Process $url | Out-Null
            $statusLabel.Text = "起動済みの画面を開きました: $url"
            return
        }
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', ('"' + $startBat + '"') -WorkingDirectory $projectRoot | Out-Null
        $statusLabel.Text = 'システムを起動しています。完了後にブラウザーが開きます。'
    }
    catch {
        Show-LauncherError 'システム起動エラー' $_.Exception
    }
} $true
$form.Controls.Add($startButton)

$openButton = New-LauncherButton '2. OCR画面を開く' 156 {
    try {
        $url = Find-FrontendUrl
        if (-not $url) {
            throw 'フロントエンドが見つかりません。先に「システムを起動」を実行してください。'
        }
        Start-Process $url | Out-Null
        $statusLabel.Text = "OCR画面を開きました: $url"
    }
    catch {
        Show-LauncherError 'OCR画面エラー' $_.Exception
    }
}
$form.Controls.Add($openButton)

$scanButton = New-LauncherButton '3. Epson Scan 2を開く' 204 {
    try {
        if (-not (Test-Path -LiteralPath $epsonScan -PathType Leaf)) {
            throw "Epson Scan 2が見つかりません: $epsonScan"
        }
        Start-Process -FilePath $epsonScan | Out-Null
        $statusLabel.Text = 'Epson Scan 2を開きました。'
    }
    catch {
        Show-LauncherError 'スキャナー起動エラー' $_.Exception
    }
}
$form.Controls.Add($scanButton)

$folderButton = New-LauncherButton '4. スキャン保存フォルダを開く' 252 {
    try {
        Ensure-ScanFolder
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $scanFolder + '"') | Out-Null
        $statusLabel.Text = "保存フォルダ: $scanFolder"
    }
    catch {
        Show-LauncherError 'フォルダーエラー' $_.Exception
    }
}
$form.Controls.Add($folderButton)

$queueButton = New-LauncherButton '5. EPSON印刷キューを開く' 300 {
    try {
        $arguments = 'printui.dll,PrintUIEntry /o /n "' + $printerName + '"'
        Start-Process -FilePath 'rundll32.exe' -ArgumentList $arguments | Out-Null
        $statusLabel.Text = 'EPSON印刷キューを開きました。'
    }
    catch {
        Show-LauncherError '印刷キューエラー' $_.Exception
    }
}
$form.Controls.Add($queueButton)

$checkButton = New-LauncherButton '6. EPSON接続状態を確認' 348 {
    try {
        $printer = Get-Printer -Name $printerName -ErrorAction Stop
        $networkOk = Test-Connection -ComputerName $scannerAddress -Count 1 -Quiet -ErrorAction SilentlyContinue
        $networkText = if ($networkOk) { '応答あり' } else { '応答なし' }
        $statusLabel.Text = "印刷: $($printer.PrinterStatus) / スキャナー: $networkText"
    }
    catch {
        Show-LauncherError '接続確認エラー' $_.Exception
    }
}
$form.Controls.Add($checkButton)

$closeButton = New-LauncherButton '閉じる' 402 { $form.Close() }
$form.Controls.Add($closeButton)

[void]$form.ShowDialog()