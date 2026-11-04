using System.Text.Json;
using Luma.Core.Contracts;
using Luma.Core.Engine;

internal static class IntegrationSuite
{
    public static async Task<int> Run(string executable, string output)
    {
        int count = 0;
        void Check(bool condition, string name) { if (!condition) throw new Exception("INTEGRATION_FAIL " + name); count++; }
        async Task Reject(Func<Task> action, string name)
        { try { await action(); throw new Exception("accepted " + name); } catch (Exception e) when (e is IOException or OperationCanceledException) { count++; } }
        Directory.CreateDirectory(output);
        string data = PreviewDirectory.Create(output);
        await using var client = new EngineClient(executable, data);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        await client.ConnectAsync(timeout.Token);
        Check(client.Connected && client.IsAlive, "real frozen child connected");
        var statusProbe = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        var statusThread = new Thread(() =>
        {
            var context = new StatusWaitContext();
            try
            {
                SynchronizationContext.SetSynchronizationContext(context);
                for (int i = 0; i < 128; i++)
                    if (!client.IsAlive || client.ProcessId is null) throw new Exception("owned child disappeared during status probe");
                statusProbe.SetResult(context.Waits);
            }
            catch (Exception error) { statusProbe.SetException(error); }
            finally { SynchronizationContext.SetSynchronizationContext(null); }
        });
        if (OperatingSystem.IsWindows()) statusThread.SetApartmentState(ApartmentState.STA);
        statusThread.Start();
        Check(await statusProbe.Task == 0, "STA status polling never enters a synchronization-context wait");
        var early = new EngineClient(executable, PreviewDirectory.Create(output));
        try
        {
            var connecting = early.ConnectAsync(timeout.Token);
            await early.DisposeAsync();
            try { await connecting; } catch (IOException) { }
            Check(!early.IsAlive && !early.Connected && early.ProcessId is null, "dispose during startup leaves no owned child or late handshake");
        }
        finally
        {
            // The failing pre-fix path can leave stdin open. Close only this
            // synthetic child's pipe, without killing it or touching a library.
            if (early.IsAlive)
            {
                var owned = typeof(EngineClient).GetField("_process", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.GetValue(early)!;
                ((Stream)typeof(EngineProcess).GetProperty("Input", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.GetValue(owned)!).Dispose();
            }
            await early.DisposeAsync();
        }
        var page = await client.QueryAsync(new(), token: timeout.Token);
        Check(page.Items.Length == 0 && page.Total == 0, "actual independent empty catalog");
        await Reject(async () => await client.QueryAsync(new(Limit: 121)), "query budget");
        await Reject(async () => await client.CallAsync("organize.execute", new { }), "dangerous method disabled");
        await Reject(async () => await client.NeighborsAsync(new(1, Before: 5)), "neighbor budget");
        var scope = new EngineContext(); scope.Advance("old");
        var old = client.CallAsync("diagnostics.wait", new { milliseconds = 300 }, scope);
        scope.Advance("new");
        var current = client.CallAsync("diagnostics.wait", new { milliseconds = 5 }, scope);
        Check((await current).GetProperty("completed").GetBoolean(), "out of order current reply");
        await Reject(async () => await old, "late context cannot publish");
        using (var cancel = new CancellationTokenSource(50))
            await Reject(async () => await client.CallAsync("diagnostics.wait", new { milliseconds = 1000 }, null, cancel.Token), "transient cancellation");
        Check((await client.CallAsync("diagnostics.echo", new { safe = true })).GetProperty("safe").GetBoolean(), "channel remains usable after cancellation");
        var jobs = Enumerable.Range(0, 20).Select(async _ => { try { await client.CallAsync("diagnostics.wait", new { milliseconds = 30 }); return true; } catch (EngineRpcException e) when (e.Code == "busy") { return false; } }).ToArray();
        bool[] admitted = await Task.WhenAll(jobs); Check(admitted.Any(x => x) && admitted.Any(x => !x), "bounded admission refuses overload");
        Check((await client.CallAsync("diagnostics.echo", new { refill = 1 })).GetProperty("refill").GetInt32() == 1, "capacity drains and refills");
        await using (var other = new EngineClient(executable, data))
            await Reject(async () => await other.ConnectAsync(timeout.Token), "two frontends cannot own one data directory");
        Check(client.Connected, "failed second owner does not disturb first");
        string prompt = string.Concat(Enumerable.Repeat("😀", 512*1024));
        var big = await client.CallAsync("diagnostics.echo", new { prompt, negative_prompt = prompt, parameters = new { raw = new string('界', 1024*1024) } });
        Check(big.GetProperty("prompt").GetString() == prompt && big.GetProperty("negative_prompt").GetString() == prompt, "retained Unicode crosses C# and Python losslessly");
        var state = await client.CallAsync("engine.status", new { operationId = "synthetic-uncertain-write" });
        Check(!state.GetProperty("writesEnabled").GetBoolean() && state.GetProperty("outcome").GetString() == "not_enabled", "uncertain writes never replay");
        try { EngineAssets.Resolve(data, new(new string('a',64), "thumbnails/../private.png")); throw new Exception("asset traversal accepted"); } catch (EngineRpcException) { count++; }
        await client.StopAsync(timeout.Token); Check(!client.IsAlive, "safe shutdown exits before new ownership");
        await using(var restart = new EngineClient(executable,data)) { await restart.ConnectAsync(timeout.Token); Check(restart.Connected,"lock released after safe stop"); }
        string fake=Path.Combine(output,"fault-engine");Directory.CreateDirectory(fake);
        foreach(string file in Directory.GetFiles(AppContext.BaseDirectory)) File.Copy(file,Path.Combine(fake,Path.GetFileName(file)),false);
        File.Copy(Path.Combine(fake,"Luma.Core.Tests.exe"),Path.Combine(fake,"Luma.Engine.exe"),false);
        foreach(string mode in new[]{"version","context"})
        {
            string faultData=PreviewDirectory.Create(output);File.WriteAllText(Path.Combine(faultData,"fake-mode.txt"),mode);
            await using var bad=new EngineClient(Path.Combine(fake,"Luma.Engine.exe"),faultData);
            await Reject(async()=>{await bad.ConnectAsync(timeout.Token);await bad.CallAsync("diagnostics.echo",new{},null,timeout.Token);},"reject wrong "+mode+" before publication");
        }
        var disconnectedEvent=typeof(EngineClient).GetEvent("Disconnected");
        Check(disconnectedEvent is not null,"idle disconnect notification exists");
        string idleData=PreviewDirectory.Create(output);File.WriteAllText(Path.Combine(idleData,"fake-mode.txt"),"idle");
        await using(var idle=new EngineClient(Path.Combine(fake,"Luma.Engine.exe"),idleData))
        {
            var notification=new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);int notifications=0;
            disconnectedEvent!.AddEventHandler(idle,(Action<string>)(code=>{Interlocked.Increment(ref notifications);notification.TrySetResult(code);}));
            await idle.ConnectAsync(timeout.Token);await idle.QueryAsync(new(),token:timeout.Token);
            await notification.Task.WaitAsync(TimeSpan.FromSeconds(3));
            Check(!idle.Connected && notifications==1,"idle fault updates consumer once");
        }
        File.WriteAllText(Path.Combine(output,"integration-report.json"), JsonSerializer.Serialize(new { result="passed", count, frozenEngine=true, syntheticOnly=true, privateDataAccess=false, forceKillUsed=false }));
        return count;
    }
    private sealed class StatusWaitContext : SynchronizationContext
    {
        public int Waits;
        public StatusWaitContext() => SetWaitNotificationRequired();
        public override int Wait(IntPtr[] waitHandles, bool waitAll, int millisecondsTimeout)
        {
            Waits++;
            return base.Wait(waitHandles, waitAll, millisecondsTimeout);
        }
    }
}
