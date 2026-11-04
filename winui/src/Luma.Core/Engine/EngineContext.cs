namespace Luma.Core.Engine;
public readonly record struct ContextStamp(long Generation, string Identity);
public sealed class EngineContext
{
    private readonly object _lock = new();
    private ContextStamp _current = new(0, "");
    public ContextStamp Advance(string identity)
    { lock (_lock) { _current = new(checked(_current.Generation + 1), identity); return _current; } }
    public ContextStamp Current { get { lock (_lock) return _current; } }
    public bool IsCurrent(ContextStamp stamp) { lock (_lock) return stamp == _current; }
    public void Invalidate() => Advance("");
}
public sealed class EngineRpcException(string code) : IOException(code)
{
    public string Code { get; } = code;
}
