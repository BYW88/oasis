#Requires -RunAsAdministrator
param([Parameter(Mandatory=$true)][string]$AccountSid)
$ErrorActionPreference = 'Stop'
if (-not ('OasisBatchLogon' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
public static class OasisBatchLogon {
  [StructLayout(LayoutKind.Sequential)] struct Attributes {
    public int Length; public IntPtr RootDirectory; public IntPtr ObjectName;
    public uint Flags; public IntPtr SecurityDescriptor; public IntPtr SecurityQualityOfService;
  }
  [StructLayout(LayoutKind.Sequential)] struct UnicodeString {
    public ushort Length; public ushort MaximumLength; public IntPtr Buffer;
  }
  [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref Attributes attributes, uint access, out IntPtr policy);
  [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr policy, IntPtr sid, UnicodeString[] rights, uint count);
  [DllImport("advapi32.dll")] static extern uint LsaNtStatusToWinError(uint status);
  [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr policy);
  static void Check(uint status) { if(status != 0) throw new Win32Exception((int)LsaNtStatusToWinError(status)); }
  public static void Grant(string accountSid) {
    var sid = new SecurityIdentifier(accountSid);
    var bytes = new byte[sid.BinaryLength]; sid.GetBinaryForm(bytes, 0);
    IntPtr policy = IntPtr.Zero, sidBuffer = IntPtr.Zero, text = IntPtr.Zero;
    try {
      var attributes = new Attributes(); attributes.Length = Marshal.SizeOf(typeof(Attributes));
      Check(LsaOpenPolicy(IntPtr.Zero, ref attributes, 0x810, out policy));
      sidBuffer = Marshal.AllocHGlobal(bytes.Length); Marshal.Copy(bytes, 0, sidBuffer, bytes.Length);
      string name = "SeBatchLogonRight"; text = Marshal.StringToHGlobalUni(name);
      var right = new UnicodeString { Length = (ushort)(name.Length * 2), MaximumLength = (ushort)((name.Length + 1) * 2), Buffer = text };
      Check(LsaAddAccountRights(policy, sidBuffer, new[] { right }, 1));
    } finally {
      if(text != IntPtr.Zero) Marshal.FreeHGlobal(text);
      if(sidBuffer != IntPtr.Zero) Marshal.FreeHGlobal(sidBuffer);
      if(policy != IntPtr.Zero) LsaClose(policy);
    }
  }
}
'@
}
[OasisBatchLogon]::Grant($AccountSid)
