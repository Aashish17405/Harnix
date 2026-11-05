param([string]$File, [string]$Title = 'Attach images to Forge', [string]$OwnerLike = '')
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
}
"@
$AE = [System.Windows.Automation.AutomationElement]
$scope = [System.Windows.Automation.TreeScope]
function Prop($p, $v) { New-Object System.Windows.Automation.PropertyCondition($p, $v) }
$dlg = $null
for ($i = 0; $i -lt 60 -and -not $dlg; $i++) {
  Start-Sleep -Milliseconds 250
  $dlg = $AE::RootElement.FindFirst($scope::Children, (Prop $AE::NameProperty $Title))
  if (-not $dlg -and $OwnerLike) {
    foreach ($w in $AE::RootElement.FindAll($scope::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
      if ($w.Current.Name -like $OwnerLike) { $dlg = $w.FindFirst($scope::Descendants, (Prop $AE::NameProperty $Title)); if ($dlg) { break } }
    }
  }
}
if (-not $dlg) { 'dialog-not-found'; exit 0 }
Start-Sleep -Milliseconds 500
# the "File name" box: a ComboBoxEx32 window with an Edit inside; set its text with a window message
$combo = $dlg.FindFirst($scope::Descendants, (Prop $AE::AutomationIdProperty '1148'))
if (-not $combo) { 'file-name-box-not-found'; exit 0 }
$edit = $combo.FindFirst($scope::Descendants, (Prop $AE::ControlTypeProperty ([System.Windows.Automation.ControlType]::Edit)))
$hEdit = if ($edit) { $edit.Current.NativeWindowHandle } else { 0 }
if (-not $hEdit) { $hEdit = $combo.Current.NativeWindowHandle }
[void][W]::SendMessage([IntPtr]$hEdit, 0x000C, [IntPtr]::Zero, $File)   # WM_SETTEXT
Start-Sleep -Milliseconds 300
# the default button ("Open", or the openLabel): a native Button window; click it with BM_CLICK
$open = $null
foreach ($n in @('Attach', 'Open')) {
  $c = New-Object System.Windows.Automation.AndCondition((Prop $AE::NameProperty $n), (Prop $AE::ClassNameProperty 'Button'))
  $open = $dlg.FindFirst($scope::Descendants, $c)
  if ($open) { break }
}
if (-not $open) { 'button-not-found'; exit 0 }
[void][W]::SendMessage([IntPtr]$open.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)   # BM_CLICK
'dialog-driven'
