# A throwaway window for end-to-end tests, so tests never touch real apps.
# Mirrors what it receives into C:\winuse\logs\testpad.json.
# -Quiet: open behind other windows without taking focus (for background-action tests).
param([int]$X = 200, [int]$Y = 200, [switch]$Quiet)

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @"
using System.Windows.Forms;
public class WinusePad : Form {
    public bool Quiet;
    protected override bool ShowWithoutActivation { get { return Quiet; } }
    protected override CreateParams CreateParams {
        get {
            CreateParams cp = base.CreateParams;
            if (QuietAtCreate) cp.ExStyle |= 0x08000000;  // WS_EX_NOACTIVATE: never becomes the active window
            return cp;
        }
    }
    public static bool QuietAtCreate;
}
"@

$statePath = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\testpad.json'
$state = @{ text = ''; clicks = 0; checked = $false; color = ''; keys = ''; closed = $false; active = $false }

[WinusePad]::QuietAtCreate = [bool]$Quiet
$form = New-Object WinusePad
$form.Quiet = [bool]$Quiet
$form.Text = 'winuse-testpad'
$form.StartPosition = 'Manual'
$form.Location = New-Object System.Drawing.Point($X, $Y)
$form.ClientSize = New-Object System.Drawing.Size(520, 250)
$form.TopMost = -not $Quiet
$form.KeyPreview = $true

$tb = New-Object System.Windows.Forms.TextBox
$tb.AccessibleName = 'Input box'
$tb.Location = New-Object System.Drawing.Point(20, 20)
$tb.Width = 480
$tb.Font = New-Object System.Drawing.Font('Segoe UI', 14)

$btn = New-Object System.Windows.Forms.Button
$btn.Text = 'Press me'
$btn.Location = New-Object System.Drawing.Point(20, 75)
$btn.Size = New-Object System.Drawing.Size(160, 44)

$cb = New-Object System.Windows.Forms.CheckBox
$cb.Text = 'Enable option'
$cb.Location = New-Object System.Drawing.Point(210, 85)
$cb.AutoSize = $true

$combo = New-Object System.Windows.Forms.ComboBox
$combo.AccessibleName = 'Color'
$combo.DropDownStyle = 'DropDownList'
[void]$combo.Items.AddRange(@('Red', 'Green', 'Blue'))
$combo.Location = New-Object System.Drawing.Point(20, 140)
$combo.Width = 160

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = 'winuse test window. Safe to close.'
$lbl.AutoSize = $true
$lbl.Location = New-Object System.Drawing.Point(20, 200)

$form.Controls.AddRange(@($tb, $btn, $cb, $combo, $lbl))

function Get-ScreenRect($c) {
    $r = $c.RectangleToScreen($c.ClientRectangle)
    return @($r.X, $r.Y, $r.Width, $r.Height)
}

function Save-State {
    $state.text = $tb.Text
    $state.checked = $cb.Checked
    $state.color = [string]$combo.SelectedItem
    $state.input_rect = Get-ScreenRect $tb
    $state.button_rect = Get-ScreenRect $btn
    $state.check_rect = Get-ScreenRect $cb
    $state.active = ([System.Windows.Forms.Form]::ActiveForm -eq $form)
    $json = $state | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($statePath, $json, (New-Object System.Text.UTF8Encoding($false)))
}

$tb.Add_TextChanged({ Save-State })
$btn.Add_Click({ $state.clicks = $state.clicks + 1; Save-State })
$cb.Add_CheckedChanged({ Save-State })
$combo.Add_SelectedIndexChanged({ Save-State })
$form.Add_KeyDown({
    param($s, $e)
    if ($e.KeyCode -eq 'F5' -or $e.KeyCode -eq 'Escape') { $state.keys = $state.keys + [string]$e.KeyCode + ' '; Save-State }
})
$form.Add_Shown({ if ($Quiet) { $form.SendToBack() }; Save-State })
$form.Add_Activated({ Save-State })
$form.Add_Deactivate({ Save-State })
$form.Add_FormClosed({ $state.closed = $true; Save-State })

[System.Windows.Forms.Application]::Run($form)
