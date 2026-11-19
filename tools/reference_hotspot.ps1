param([ValidateSet('Inspect','Start','Resume','KeepOn','Stop')][string]$Action='Inspect')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$wmpRoot=Split-Path -Parent $PSScriptRoot
$wmpStatePath=Join-Path $wmpRoot 'hardware\reference-ap.json'
$wmpBackupPath=Join-Path $wmpRoot 'hardware\reference-ap-original.xml'
$wmpProfile=[Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]::GetInternetConnectionProfile()
$wmpType=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$wmpConfigType=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$wmpBandType=[Windows.Networking.NetworkOperators.TetheringWiFiBand,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$wmpResultType=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$wmpManager=$wmpType::CreateFromConnectionProfile($wmpProfile)
function Wait-WmpAction($Operation) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.FullName -eq 'Windows.Foundation.IAsyncAction' } | Select-Object -First 1
    $task=$method.Invoke($null,@($Operation))
    if(-not $task.Wait(30000)){throw 'WinRT operation timed out; inspect state before any further action'}
    $task.GetAwaiter().GetResult()
}
function Wait-WmpResult($Operation) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
    $task=$method.MakeGenericMethod(@($wmpResultType)).Invoke($null,@($Operation))
    if(-not $task.Wait(30000)){throw 'WinRT operation timed out; inspect state before any further action'}
    $task.GetAwaiter().GetResult()
}
function Save-WmpState($Value) { $Value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $wmpStatePath -Encoding UTF8 }
if($Action -eq 'Inspect') {
    [pscustomobject]@{Capability=$wmpType::GetTetheringCapabilityFromConnectionProfile($wmpProfile).ToString();State=$wmpManager.TetheringOperationalState.ToString();Clients=$wmpManager.ClientCount;MaximumClients=$wmpManager.MaxClientCount;NoConnectionsTimeoutEnabled=$wmpType::IsNoConnectionsTimeoutEnabled();AuthenticationKinds=@([Enum]::GetNames($wmpManager.GetCurrentAccessPointConfiguration().AuthenticationKind.GetType()))} | ConvertTo-Json -Compress
    exit
}
if($Action -eq 'KeepOn') {
    if(-not (Test-Path -LiteralPath $wmpStatePath)){throw 'No owned reference configuration'}
    $state=Get-Content -LiteralPath $wmpStatePath -Raw | ConvertFrom-Json
    $current=$wmpManager.GetCurrentAccessPointConfiguration()
    if($state.state -ne 'active' -or -not $state.diagnostic_only -or $current.Ssid -ne $state.ssid -or $current.Passphrase -ne $state.passphrase -or $current.Band.ToString() -ne 'TwoPointFourGigahertz' -or $current.AuthenticationKind.ToString() -ne 'Wpa2'){throw 'AP settings are not the unchanged owned reference'}
    if(-not ($state.PSObject.Properties.Name -contains 'original_no_connections_timeout')){
        $state | Add-Member -NotePropertyName original_no_connections_timeout -NotePropertyValue ($wmpType::IsNoConnectionsTimeoutEnabled())
    }
    Save-WmpState $state
    $wmpType::DisableNoConnectionsTimeout()
    if($wmpType::IsNoConnectionsTimeoutEnabled()){throw 'No-connections timeout did not disable'}
    [pscustomobject]@{timeout_disabled=$true;original_timeout_enabled=$state.original_no_connections_timeout;owned_reference_only=$true} | ConvertTo-Json -Compress
    exit
}
if($Action -eq 'Resume') {
    if(-not (Test-Path -LiteralPath $wmpStatePath)){throw 'No owned AP configuration to resume'}
    $state=Get-Content -LiteralPath $wmpStatePath -Raw | ConvertFrom-Json
    $current=$wmpManager.GetCurrentAccessPointConfiguration()
    if($state.state -ne 'active' -or -not $state.diagnostic_only -or $current.Ssid -ne $state.ssid -or $current.Passphrase -ne $state.passphrase -or $current.Band.ToString() -ne 'TwoPointFourGigahertz' -or $current.AuthenticationKind.ToString() -ne 'Wpa2'){throw 'Current AP configuration is not the unchanged owned reference'}
    if($wmpManager.TetheringOperationalState.ToString() -eq 'On'){[pscustomobject]@{state='active';already_on=$true;diagnostic_only=$true}|ConvertTo-Json -Compress;exit}
    if($wmpManager.TetheringOperationalState.ToString() -ne 'Off'){throw 'AP operation is still pending; inspect before any replay'}
    $result=Wait-WmpResult ($wmpManager.StartTetheringAsync())
    if($result.Status.ToString() -ne 'Success'){throw ('Owned AP resume failed: '+$result.Status.ToString())}
    [pscustomobject]@{state='active';resumed=$true;diagnostic_only=$true}|ConvertTo-Json -Compress
    exit
}
if($Action -eq 'Start') {
    if($wmpManager.TetheringOperationalState.ToString() -ne 'Off'){throw 'Existing tethering must remain untouched'}
    if($wmpProfile.IsWlanConnectionProfile){throw 'Reference expects the existing Ethernet internet profile'}
    if(Test-Path -LiteralPath $wmpStatePath){
        $prior=Get-Content -LiteralPath $wmpStatePath -Raw | ConvertFrom-Json
        if($prior.state -ne 'restored'){throw 'Previous test has not been restored; inspect its state first'}
    }
    $saved=$wmpManager.GetCurrentAccessPointConfiguration()
    # DPAPI protects the prior system passphrase; only the current user can
    # restore it. Neither old nor generated credentials are printed.
    [pscustomobject]@{Ssid=$saved.Ssid;Passphrase=(ConvertTo-SecureString $saved.Passphrase -AsPlainText -Force);Band=$saved.Band.ToString();AuthenticationKind=$saved.AuthenticationKind.ToString()} | Export-Clixml -LiteralPath $wmpBackupPath
    $config=[Activator]::CreateInstance($wmpConfigType)
    $config.Ssid='WMP-Lab-'+[Guid]::NewGuid().ToString('N').Substring(0,6)
    $config.Passphrase=[Guid]::NewGuid().ToString('N').Substring(0,20)
    $config.Band=[Enum]::Parse($wmpBandType,'TwoPointFourGigahertz')
    $config.AuthenticationKind=[Enum]::Parse($config.AuthenticationKind.GetType(),'Wpa2')
    $state=[ordered]@{state='configuring';ssid=$config.Ssid;passphrase=$config.Passphrase;band='2.4GHz';maximum_clients=$wmpManager.MaxClientCount;diagnostic_only=$true;started_utc=[DateTime]::UtcNow.ToString('o')}
    Save-WmpState $state
    Wait-WmpAction ($wmpManager.ConfigureAccessPointAsync($config))
    $state.state='starting';Save-WmpState $state
    $result=Wait-WmpResult ($wmpManager.StartTetheringAsync())
    if($result.Status.ToString() -ne 'Success'){$state.state='start_failed';$state.status=$result.Status.ToString();Save-WmpState $state;throw ('Tethering failed: '+$result.Status.ToString())}
    $state.state='active';Save-WmpState $state
    [pscustomobject]@{state=$state.state;band=$state.band;maximum_clients=$state.maximum_clients;diagnostic_only=$true} | ConvertTo-Json -Compress
    exit
}
if(-not (Test-Path -LiteralPath $wmpStatePath)){throw 'No owned reference AP state'}
$state=Get-Content -LiteralPath $wmpStatePath -Raw | ConvertFrom-Json
$current=$wmpManager.GetCurrentAccessPointConfiguration()
if($state.state -ne 'active' -or -not $state.diagnostic_only -or $current.Ssid -ne $state.ssid -or $current.Passphrase -ne $state.passphrase){throw 'Refuse restoration after owned AP configuration changed'}
if($state.PSObject.Properties.Name -contains 'original_no_connections_timeout'){
    if($state.original_no_connections_timeout){$wmpType::EnableNoConnectionsTimeout()}else{$wmpType::DisableNoConnectionsTimeout()}
    if($wmpType::IsNoConnectionsTimeoutEnabled() -ne [bool]$state.original_no_connections_timeout){throw 'Original no-connections policy not restored'}
}
if($wmpManager.TetheringOperationalState.ToString() -ne 'Off'){
    if($wmpManager.GetCurrentAccessPointConfiguration().Ssid -ne $state.ssid){throw 'Active AP is not the test-owned AP'}
    $result=Wait-WmpResult ($wmpManager.StopTetheringAsync())
    if($result.Status.ToString() -ne 'Success'){throw ('Stop failed: '+$result.Status.ToString())}
}
$saved=Import-Clixml -LiteralPath $wmpBackupPath
$restore=[Activator]::CreateInstance($wmpConfigType);$restore.Ssid=$saved.Ssid
$credential=New-Object System.Management.Automation.PSCredential('reference-restore',$saved.Passphrase)
$restore.Passphrase=$credential.GetNetworkCredential().Password;$restore.Band=[Enum]::Parse($wmpBandType,$saved.Band)
$restore.AuthenticationKind=[Enum]::Parse($restore.AuthenticationKind.GetType(),$saved.AuthenticationKind)
Wait-WmpAction ($wmpManager.ConfigureAccessPointAsync($restore))
$state.state='restored';$state.passphrase=$null;Save-WmpState $state
[pscustomobject]@{state='restored';ap_state=$wmpManager.TetheringOperationalState.ToString()} | ConvertTo-Json -Compress
