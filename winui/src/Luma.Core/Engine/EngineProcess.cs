using System.Diagnostics;

namespace Luma.Core.Engine;

public sealed class EngineProcess
{
    private sealed record ChildState(Process Process, int Id, bool Alive);
    private ChildState? _state;
    private int _exitCode;
    private Task? _stderr;
    private int _inputClosed;
    private string _lastCode = "engine_disconnected";
    private static readonly HashSet<string> Codes = ["engine_busy", "unsafe_data_directory", "recovery_required", "catalog_unavailable", "engine_failed", "protocol_error", "protocol_mismatch", "invalid_request"];
    public string LastCode => _lastCode;
    // UI status reads must never enter Process.HasExited/WaitOne: an STA wait can
    // pump XAML messages from inside a protected layout/input callback.
    public bool IsAlive => Volatile.Read(ref _state)?.Alive == true;
    public int? ProcessId => Volatile.Read(ref _state)?.Id;
    internal Stream Input => Volatile.Read(ref _state)!.Process.StandardInput.BaseStream;
    internal Stream Output => Volatile.Read(ref _state)!.Process.StandardOutput.BaseStream;

    internal Task StartAsync(string executable, string directory, bool prepareDemo = false,string? singleFile=null)
        => Task.Run(() => StartCore(executable, directory, prepareDemo, singleFile));
    private void StartCore(string executable, string directory, bool prepareDemo,string? singleFile)
    {
        if (!Path.IsPathFullyQualified(executable) || Path.GetFileName(executable) != "Luma.Engine.exe" || !File.Exists(executable)) throw new EngineRpcException("engine_missing");
        if (!Path.IsPathFullyQualified(directory)) throw new EngineRpcException("unsafe_data_directory");
        RejectReparse(executable); RejectReparse(directory);
        var info = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Path.GetDirectoryName(executable)! };
        info.ArgumentList.Add("--data-dir"); info.ArgumentList.Add(directory);
        string runtime=Path.Combine(directory,"runtime","tmp");RejectReparse(runtime);Directory.CreateDirectory(runtime);
        info.Environment["TEMP"]=runtime;info.Environment["TMP"]=runtime;
        info.Environment["HF_HOME"]=Path.Combine(directory,"runtime","huggingface");
        info.Environment["XDG_CACHE_HOME"]=Path.Combine(directory,"runtime","cache");
        if(prepareDemo)info.ArgumentList.Add("--seed-demo");
        if(singleFile is not null){info.ArgumentList.Add("--seed-file");info.ArgumentList.Add(singleFile);}
        info.Environment.Remove("PYTHONPATH"); info.Environment.Remove("PYTHONHOME"); info.Environment["PYTHONNOUSERSITE"] = "1";
        var process = Process.Start(info) ?? throw new EngineRpcException("engine_failed");
        TrackProcess(process);
        _stderr = DrainErrorsAsync(process.StandardError.BaseStream);
    }

    private void TrackProcess(Process process)
    {
        // Publish before enabling events; an already-exited child can report its
        // exit synchronously while EnableRaisingEvents is being set (off the UI).
        Volatile.Write(ref _state, new ChildState(process, process.Id, true));
        process.Exited += (_, _) => MarkExited(process);
        process.EnableRaisingEvents = true;
    }
    private void MarkExited(Process process)
    {
        while (Volatile.Read(ref _state) is { Alive: true } state && ReferenceEquals(state.Process, process))
            if (ReferenceEquals(Interlocked.CompareExchange(ref _state, state with { Alive = false }, state), state)) return;
    }

    private async Task DrainErrorsAsync(Stream input)
    {
        byte[] buffer = new byte[1024]; char[] line = new char[64]; int used = 0; bool oversized = false;
        try
        {
            int count;
            while ((count = await input.ReadAsync(buffer).ConfigureAwait(false)) != 0)
                foreach (byte value in buffer.AsSpan(0, count))
                {
                    if (value == 10) { string code = new(line, 0, used); if (!oversized && Codes.Contains(code)) _lastCode = code; used = 0; oversized = false; }
                    else if (used < line.Length && value >= 32 && value < 127) line[used++] = (char)value;
                    else oversized = true;
                }
        }
        catch (IOException) { }
    }

    public void CloseInput()
    {
        if(Interlocked.Exchange(ref _inputClosed,1)!=0)return;
        try { Volatile.Read(ref _state)?.Process.StandardInput.Close(); }
        catch(Exception error) when(error is IOException or ObjectDisposedException or InvalidOperationException) { }
    }
    public static async Task PrepareDemoAsync(string executable,string directory)
    {
        var process=new EngineProcess();await process.StartAsync(executable,directory,true).ConfigureAwait(false);process.CloseInput();
        // Preparation writes only fresh synthetic data. Never kill it on a timeout.
        await process.WaitForExitAsync().ConfigureAwait(false);
        int code=process._exitCode;await process.ReleaseExitedAsync().ConfigureAwait(false);
        if(code!=0)throw new EngineRpcException("preview_prepare_failed");
    }
    public static async Task PrepareSingleAsync(string executable,string directory,string file)
    {
        if(!Path.IsPathFullyQualified(file))throw new EngineRpcException("invalid_request");
        var process=new EngineProcess();await process.StartAsync(executable,directory,singleFile:file).ConfigureAwait(false);process.CloseInput();
        await process.WaitForExitAsync().ConfigureAwait(false);int code=process._exitCode;await process.ReleaseExitedAsync().ConfigureAwait(false);
        if(code!=0)throw new EngineRpcException("single_file_failed");
    }
    public async Task WaitForExitAsync(CancellationToken cancellationToken = default)
    {
        var state=Volatile.Read(ref _state);if(state is null)return;
        await Task.Run(async () =>
        {
            // Even the synchronous prefix of Process.WaitForExitAsync stays off
            // an STA. Cancellation never publishes an exit or releases a child.
            await state.Process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            _exitCode=state.Process.ExitCode;MarkExited(state.Process);
        }).ConfigureAwait(false);
        if (_stderr is not null) await _stderr.ConfigureAwait(false);
    }
    internal Task ReleaseExitedAsync()
    {
        var state=Volatile.Read(ref _state);
        if(state is null||state.Alive||!ReferenceEquals(Interlocked.CompareExchange(ref _state,null,state),state))return Task.CompletedTask;
        return Task.Run(state.Process.Dispose);
    }

    public static void RejectReparse(string path)
    {
        for (string? current = Path.GetFullPath(path); current is not null; current = Path.GetDirectoryName(current))
            if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new EngineRpcException("asset_boundary");
    }
}
