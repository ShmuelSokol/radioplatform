# Pops a native dialog, writes the pasted key to backend/.env (gitignored), never prints it.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envPath = Join-Path $root "backend\.env"

$form = New-Object System.Windows.Forms.Form
$form.Text = "TypeSafe API Key"
$form.Size = New-Object System.Drawing.Size(520, 190)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = "Paste your TypeSafe (Jev) API key. It will be saved to backend\.env and sent to Railway."
$label.Location = New-Object System.Drawing.Point(15, 15)
$label.Size = New-Object System.Drawing.Size(480, 40)
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Location = New-Object System.Drawing.Point(15, 60)
$box.Size = New-Object System.Drawing.Size(475, 25)
$box.UseSystemPasswordChar = $true
$form.Controls.Add($box)

$show = New-Object System.Windows.Forms.CheckBox
$show.Text = "Show"
$show.Location = New-Object System.Drawing.Point(15, 92)
$show.Add_CheckedChanged({ $box.UseSystemPasswordChar = -not $show.Checked })
$form.Controls.Add($show)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "Save"
$ok.Location = New-Object System.Drawing.Point(310, 110)
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($ok)
$form.AcceptButton = $ok

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Cancel"
$cancel.Location = New-Object System.Drawing.Point(400, 110)
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)
$form.CancelButton = $cancel

$form.Add_Shown({ $form.Activate(); $box.Focus() })
$result = $form.ShowDialog()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) { Write-Output "CANCELLED"; exit 1 }
$key = $box.Text.Trim()
if ($key.Length -lt 8) { Write-Output "EMPTY"; exit 1 }

$lines = @()
if (Test-Path $envPath) { $lines = Get-Content $envPath | Where-Object { $_ -notmatch '^TYPESAFE_API_KEY=' } }
$lines += "TYPESAFE_API_KEY=$key"
[System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("SAVED length=" + $key.Length + " prefix=" + $key.Substring(0,3))
