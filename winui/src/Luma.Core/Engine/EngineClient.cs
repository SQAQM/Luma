using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using Luma.Core.Contracts;

namespace Luma.Core.Engine;

public sealed class EngineClient(string executable, string directory) : IAsyncDisposable
{
    private sealed class Pending(string method, object payload, EngineContext? context)
    {
        public readonly string Id = Guid.NewGuid().ToString("N");
        public readonly string Method = method;
        public readonly object Payload = payload;
        public readonly EngineContext? Context = context;
        public readonly ContextStamp Stamp = context?.Current ?? new(0, "");
        public readonly TaskCompletionSource<JsonElement> Server = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public readonly TaskCompletionSource<JsonElement> User = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public volatile bool Sent;
    }
    private readonly EngineProcess _process = new();
    private readonly ConcurrentDictionary<string, Pending> _pending = new();
    private readonly Channel<Pending> _queue = Channel.CreateBounded<Pending>(new BoundedChannelOptions(8) { FullMode = BoundedChannelFullMode.Wait });
    private readonly SemaphoreSlim _writer = new(1, 1);
    private Task? _reader;
    private Task[] _pumps = [];
    private volatile bool _started;
    private int _startClaimed;
    private readonly TaskCompletionSource<bool> _initialized = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private volatile bool _connected, _stopping;
    private int _faultNotified;
    private static readonly HashSet<string> Methods = ["catalog.query", "catalog.stats", "catalog.facets", "catalog.roots", "catalog.detail", "catalog.neighbors", "catalog.thumbnail", "catalog.preview.v2", "catalog.preview.release", "job.get", "job.cancel", "engine.status", "diagnostics.echo", "diagnostics.wait", "manage.read", "manage.write", "manage.status"];
    private static readonly HashSet<string> ErrorCodes = ["invalid_request", "method_not_allowed", "cancelled", "asset_boundary", "not_found", "busy", "recovering", "closed", "operation_failed", "response_invalid", "response_too_large","stale_identity","stale_cursor","unsupported","conflict","not_ready","invalid_token","model_hash_missing","stale_review","confirmation_required","recovery_required","duplicate_recovery"];
    public bool IsAlive => _process.IsAlive;
    public int? ProcessId => _process.ProcessId;
    public bool Connected => _connected && IsAlive && !_stopping;
    public bool ManagementEnabled{get;private set;}
    public bool FileOperationsEnabled{get;private set;}
    public string EngineVersion{get;private set;}="unknown";
    public event Action? CapacityAvailable;
    public event Action<string>? Disconnected;

    public async Task ConnectAsync(CancellationToken cancellationToken = default)
    {
        if (_stopping || Interlocked.CompareExchange(ref _startClaimed,1,0)!=0) throw new EngineRpcException("already_started");
        _started = true;
        try
        {
            if(_stopping)throw new EngineRpcException("engine_disconnected");
            await _process.StartAsync(executable, directory).ConfigureAwait(false);
            _reader = ReadLoopAsync();
            _pumps = Enumerable.Range(0, 2).Select(_ => PumpAsync()).ToArray();
        }
        catch { _process.CloseInput(); throw; }
        finally { _initialized.TrySetResult(true); }
        if(_stopping)throw new EngineRpcException("engine_disconnected");
        try
        {
            var hello = await ControlAsync("engine.hello", new { protocol = 1 }, cancellationToken).ConfigureAwait(false);
            if (hello.GetProperty("protocol").GetInt32() != 1 || !hello.GetProperty("preview").GetBoolean() || hello.GetProperty("writesEnabled").GetBoolean() || hello.GetProperty("maxFrameBytes").GetInt32() != FrameCodec.MaxFrame) throw new EngineProtocolException("protocol_mismatch");
            if(hello.TryGetProperty("managementWrites",out var management)&&management.ValueKind==JsonValueKind.True&&hello.TryGetProperty("capabilities",out var capabilities)&&capabilities.ValueKind==JsonValueKind.Array&&capabilities.GetArrayLength()<=64)
            {var names=capabilities.EnumerateArray().Where(e=>e.ValueKind==JsonValueKind.String).Select(e=>e.GetString()).ToHashSet();ManagementEnabled=names.Contains("manage.read")&&names.Contains("manage.write")&&names.Contains("manage.status");}
            FileOperationsEnabled=ManagementEnabled&&hello.TryGetProperty("fileOperations",out var files)&&files.ValueKind==JsonValueKind.True&&hello.TryGetProperty("destructiveWrites",out var destructive)&&destructive.ValueKind==JsonValueKind.True;
            if(hello.TryGetProperty("engineVersion",out var engineVersion)&&engineVersion.ValueKind==JsonValueKind.String&&engineVersion.GetString() is string version&&version.Length<=64&&version.All(c=>char.IsAsciiLetterOrDigit(c)||c is '.' or '+' or '-'))EngineVersion=version;
            if(_stopping)throw new EngineRpcException("engine_disconnected");
            _connected = true;
        }
        catch
        {
            _process.CloseInput();
            throw;
        }
    }

    public Task<JsonElement> CallAsync(string method, object payload, EngineContext? context = null, CancellationToken cancellationToken = default)
    {
        if (!Connected) return Task.FromException<JsonElement>(new EngineRpcException("engine_disconnected"));
        if (!Methods.Contains(method)) return Task.FromException<JsonElement>(new EngineRpcException("writes_not_enabled"));
        if(method.StartsWith("manage.",StringComparison.Ordinal)&&!ManagementEnabled)return Task.FromException<JsonElement>(new EngineRpcException("method_not_allowed"));
        var pending = new Pending(method, payload, context);
        if (Encoding.UTF8.GetByteCount(pending.Stamp.Identity) > 512) return Task.FromException<JsonElement>(new EngineRpcException("invalid_request"));
        if (!_queue.Writer.TryWrite(pending)) return Task.FromException<JsonElement>(new EngineRpcException("busy"));
        return AwaitUserAsync(pending, cancellationToken);
    }

    private async Task<JsonElement> AwaitUserAsync(Pending pending, CancellationToken cancellationToken)
    {
        using var registration = cancellationToken.Register(() =>
        {
            pending.User.TrySetCanceled(cancellationToken);
            if (pending.Sent) _ = SendCancelAsync(pending.Id);
        });
        return await pending.User.Task.ConfigureAwait(false);
    }

    private async Task SendCancelAsync(string id)
    {
        try
        {
            var message = new { version = 1, requestId = Guid.NewGuid().ToString("N"), generation = 0L, identity = "", method = "request.cancel", payload = new { requestId = id } };
            await WriteAsync(message, CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception error) when (error is IOException or ObjectDisposedException or InvalidOperationException) { Fault(); }
    }

    private async Task PumpAsync()
    {
        await foreach (var pending in _queue.Reader.ReadAllAsync().ConfigureAwait(false))
        {
            if (_stopping || pending.User.Task.IsCanceled) { pending.User.TrySetException(new EngineRpcException("closed")); CapacityAvailable?.Invoke(); continue; }
            try
            {
                _pending[pending.Id] = pending;
                await WriteAsync(Envelope(pending), CancellationToken.None).ConfigureAwait(false);
                pending.Sent = true;
                if (pending.User.Task.IsCanceled) _ = SendCancelAsync(pending.Id);
                JsonElement result = await pending.Server.Task.ConfigureAwait(false);
                if (pending.Context is not null && !pending.Context.IsCurrent(pending.Stamp)) throw new EngineRpcException("stale_context");
                pending.User.TrySetResult(result);
            }
            catch (Exception error)
            {
                pending.User.TrySetException(error is EngineRpcException or EngineProtocolException ? error : new EngineRpcException("engine_disconnected"));
            }
            finally { _pending.TryRemove(pending.Id, out _); CapacityAvailable?.Invoke(); }
        }
    }

    private static object Envelope(Pending value) => new { version = 1, requestId = value.Id, generation = value.Stamp.Generation, identity = value.Stamp.Identity, method = value.Method, payload = value.Payload };

    private async Task WriteAsync<T>(T message, CancellationToken token)
    {
        byte[] framed = FrameCodec.Encode(message);
        await _writer.WaitAsync(token).ConfigureAwait(false);
        try
        {
            // Never cancel half a frame. A transport fault closes the pipe instead.
            await _process.Input.WriteAsync(framed).ConfigureAwait(false);
            await _process.Input.FlushAsync().ConfigureAwait(false);
        }
        finally { _writer.Release(); }
    }

    private async Task<JsonElement> ControlAsync(string method, object payload, CancellationToken token)
    {
        var pending = new Pending(method, payload, null); _pending[pending.Id] = pending;
        try { await WriteAsync(Envelope(pending), token).ConfigureAwait(false); return await pending.Server.Task.WaitAsync(token).ConfigureAwait(false); }
        finally { _pending.TryRemove(pending.Id, out _); }
    }

    private async Task ReadLoopAsync()
    {
        string? failure = null;
        try
        {
            while (true)
            {
                using var document = await FrameCodec.ReadAsync(_process.Output).ConfigureAwait(false);
                if (document is null) break;
                var root = document.RootElement;
                if(root.GetProperty("version").GetInt32() != 1) throw new EngineProtocolException("protocol_mismatch");
                if (root.EnumerateObject().Count() != 6 || !root.GetProperty("generation").TryGetInt64(out long generation)) throw new EngineProtocolException();
                string id = root.GetProperty("requestId").GetString()!, identity = root.GetProperty("identity").GetString()!, kind = root.GetProperty("kind").GetString()!;
                if (kind is not ("result" or "error") || string.IsNullOrEmpty(id) || id.Length > 64) throw new EngineProtocolException();
                if (!_pending.TryGetValue(id, out var pending)) continue; // Cancel ACK or late cancelled reply; never publish it.
                if (generation != pending.Stamp.Generation || identity != pending.Stamp.Identity) throw new EngineProtocolException("invalid_context_echo");
                var payload = root.GetProperty("payload");
                if (kind == "error")
                {
                    string code = payload.GetProperty("code").GetString() ?? "operation_failed";
                    pending.Server.TrySetException(new EngineRpcException(ErrorCodes.Contains(code) ? code : "operation_failed"));
                }
                else pending.Server.TrySetResult(payload.Clone());
            }
        }
        catch(EngineProtocolException error) { failure = error.Code; }
        catch (Exception error) when (error is IOException or JsonException or InvalidOperationException or KeyNotFoundException or ObjectDisposedException) { failure = "protocol_error"; }
        finally { Fault(failure); }
    }

    private void Fault(string? code = null)
    {
        _connected = false; _queue.Writer.TryComplete();
        foreach (var pending in _pending.Values) pending.Server.TrySetException(new EngineRpcException(code ?? _process.LastCode));
        _process.CloseInput();
        if (Interlocked.Exchange(ref _faultNotified, 1) == 0)
            foreach (var callback in Disconnected?.GetInvocationList() ?? [])
                try { ((Action<string>)callback)(code ?? _process.LastCode); } catch (Exception) { /* Observer cannot break pipe draining. */ }
    }

    public async Task<QueryPage> QueryAsync(QueryRequest request, EngineContext? context = null, CancellationToken token = default)
    {
        if (request.Limit is < 1 or > 120) throw new EngineRpcException("invalid_request");
        var result = await CallAsync("catalog.query", new { filters = request.Filters ?? [], cursor = request.Cursor, limit = request.Limit }, context, token).ConfigureAwait(false);
        return result.Deserialize<QueryPage>(FrameCodec.Json) ?? throw new EngineProtocolException();
    }
    public Task<JsonElement> DetailAsync(long imageId, EngineContext? context = null, CancellationToken token = default) => CallAsync("catalog.detail", new { imageId }, context, token);
    public Task<JsonElement> NeighborsAsync(NeighborRequest request, EngineContext? context = null, CancellationToken token = default) => CallAsync("catalog.neighbors", new { request.ImageId, filters = request.Filters ?? [], request.Before, request.After }, context, token);
    public async Task<CachedAsset> ThumbnailAsync(long imageId, int size = 400, EngineContext? context = null, CancellationToken token = default)
    { var result = await CallAsync("catalog.thumbnail", new { imageId, size }, context, token).ConfigureAwait(false); return result.Deserialize<CachedAsset>(FrameCodec.Json) ?? throw new EngineProtocolException(); }
    public Task<JsonElement> GetJobAsync(string jobId, CancellationToken token = default) => CallAsync("job.get", new { jobId }, null, token);
    public Task<JsonElement> CancelJobAsync(string jobId, CancellationToken token = default) => CallAsync("job.cancel", new { jobId }, null, token);

    public async Task StopAsync(CancellationToken token = default)
    {
        _stopping = true; _queue.Writer.TryComplete();
        if(!_started)return;
        // Wait for ownership and pipe pumps, not for hello: a stopped connection
        // must still close stdin when hello is unavailable or startup is pending.
        await _initialized.Task.ConfigureAwait(false);
        try { if (IsAlive && _connected) await ControlAsync("engine.shutdown", new { }, token).ConfigureAwait(false); }
        catch (Exception error) when(error is IOException or ObjectDisposedException) { }
        finally { _process.CloseInput(); }
        await _process.WaitForExitAsync(token).ConfigureAwait(false);
        _connected = false;
        if (_reader is not null) await _reader.ConfigureAwait(false);
        if (_pumps.Length > 0) await Task.WhenAll(_pumps).ConfigureAwait(false);
        await _process.ReleaseExitedAsync().ConfigureAwait(false);
    }
    public async ValueTask DisposeAsync() { await StopAsync().ConfigureAwait(false); }
}
