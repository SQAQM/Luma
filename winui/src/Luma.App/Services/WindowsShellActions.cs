using System.Runtime.InteropServices;
using Luma.Core.Engine;
using Luma.Core.Gallery;
using Windows.ApplicationModel.DataTransfer;
namespace Luma.Preview.Services;

internal sealed class WindowsShellActions(bool diagnostics)
{
    public string LastAction{get;private set;}="";
    public int LastCopyLength{get;private set;}
    public string LastCopyHash{get;private set;}="";
    public void Copy(string value,string action)
    {
        if(!diagnostics){var package=new DataPackage();package.SetText(value);Clipboard.SetContent(package);}
        LastAction=action;LastCopyLength=value.Length;LastCopyHash=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
    }
    public Task ExecuteAsync(string action,GalleryDetail detail)=>ExecuteIntentAsync(ShellIntent.Create(action,detail.Path));
    public Task ExecuteModelAsync(string action,string path)=>ExecuteIntentAsync(ShellIntent.CreateModel(action,path));
    private async Task ExecuteIntentAsync(ShellIntent intent)
    {
        string action=intent.Action;
        if(action=="copy"){Copy(intent.Path,"path");return;}
        string executedAction=action;
        await Task.Run(()=>
        {
            EngineProcess.RejectReparse(intent.Path);
            if(!File.Exists(intent.Path))throw new EngineRpcException("not_found");
            if(diagnostics)return; // Test sink records a validated intent without activating another app.
            int initialized=CoInitializeEx(0,2);
            if(initialized<0&&initialized!=unchecked((int)0x80010106))Marshal.ThrowExceptionForHR(initialized);
            try
            {
                if(action=="select")executedAction=ShellReveal.Execute(intent.Path,SelectFile,folder=>{if((long)ShellExecuteW(0,"open",folder,null,null,1)<=32)throw new EngineRpcException("shell_failed");});
                else if((long)ShellExecuteW(0,"open",action=="folder"?Path.GetDirectoryName(intent.Path)!:intent.Path,null,null,1)<=32)throw new EngineRpcException("shell_failed");
            }
            finally{if(initialized>=0)CoUninitialize();}
        });
        LastAction=executedAction;
    }
    private static void SelectFile(string path)
    {
        nint folder=0,item=0;
        try
        {
            Marshal.ThrowExceptionForHR(SHParseDisplayName(Path.GetDirectoryName(path)!,0,out folder,0,out _));
            Marshal.ThrowExceptionForHR(SHParseDisplayName(path,0,out item,0,out _));
            nint[] children=[ILFindLastID(item)];Marshal.ThrowExceptionForHR(SHOpenFolderAndSelectItems(folder,1,children,0));
        }
        finally{if(folder!=0)Marshal.FreeCoTaskMem(folder);if(item!=0)Marshal.FreeCoTaskMem(item);}
    }
    [DllImport("shell32.dll",CharSet=CharSet.Unicode)]private static extern nint ShellExecuteW(nint hwnd,string operation,string file,string? parameters,string? directory,int show);
    [DllImport("ole32.dll")]private static extern int CoInitializeEx(nint reserved,uint flags);
    [DllImport("ole32.dll")]private static extern void CoUninitialize();
    [DllImport("shell32.dll",CharSet=CharSet.Unicode)]private static extern int SHParseDisplayName(string name,nint context,out nint pidl,uint attributes,out uint actual);
    [DllImport("shell32.dll")]private static extern nint ILFindLastID(nint pidl);
    [DllImport("shell32.dll")]private static extern int SHOpenFolderAndSelectItems(nint folder,uint count,[In]nint[] children,uint flags);
}
