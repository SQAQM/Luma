using Luma.Core.Engine;
namespace Luma.Preview.Services;

// UI-dispatcher owned; disk, startup and pipe operations run on background tasks.
internal sealed class EngineSession(string previewDirectory,bool diagnostics=false)
{
    private readonly Microsoft.UI.Dispatching.DispatcherQueue _dispatcher = Microsoft.UI.Dispatching.DispatcherQueue.GetForCurrentThread();
    private EngineClient? _client;
    private Task? _connection, _stop;
    private CancellationTokenSource? _request;
    private bool _connecting, _stopping, _closing,_servicesOnly,_openGalleryAfterConnect,_startupCatalogQueried;
    public event Action? Changed;
    public string State { get; private set; } = "未连接";
    public string Feedback { get; private set; } = "本地服务尚未启动。";
    public bool Connecting=>_connecting;
    public bool Started { get; private set; }
    public bool EmptyQueryVerified { get; private set; }
    public bool IsGallery { get; private set; }
    private string? _singleFile;
    private string? _nextFile;
    private Task? _fileOpening;
    public bool IsSingleFile=>_singleFile is not null;
    public Task OpenFileAsync(string file)
    {
        if(_closing||StorageBusy||RestartRequired){Feedback="当前窗口正在关闭或处理数据，请稍后重新选择图片。";Notify();return Task.CompletedTask;}
        _nextFile=Path.GetFullPath(file);
        if(_fileOpening is not null)return _fileOpening;
        return _fileOpening=OpenSelectedFilesAsync();
    }
    private async Task OpenSelectedFilesAsync()
    {
        // Keep the most recent explicit choice while startup releases the previous slot.
        await Task.Yield();
        try{while(_nextFile is not null&&!_closing){string file=_nextFile;_nextFile=null;_singleFile=null;await StopAsync();if(_closing||IsAlive)return;if(_nextFile is not null)continue;_singleFile=file;ConnectCore(true,false);if(_connection is not null)await _connection;}}
        finally{_fileOpening=null;}
    }
    public async Task CloseSingleAsync(){if(_closing)return;_singleFile=null;IsGallery=false;await StopAsync();}
    public bool StorageBusy{get;set;}
    public bool RestartRequired{get;set;}
    public bool CanStore=>!_connecting&&!_stopping&&!IsAlive;
    public string? DataDirectory { get; private set; }
    public EngineClient? Client => Connected ? _client : null;
    public bool Connected => _client?.Connected == true && !_connecting && !_stopping;
    public bool IsAlive => _client?.IsAlive == true;
    public bool CanConnect => !_closing && !_connecting && !_stopping && !IsAlive&&!StorageBusy&&!RestartRequired;
    public bool CanProbe => Connected && _request is null && !_closing;
    public bool CanCancel => _request is not null && !_request.IsCancellationRequested;
    public bool CanStop => !_connecting && !_stopping && IsAlive;
    public object Snapshot => new { state = State, connected = Connected, alive = IsAlive, started = Started, processId = _client?.ProcessId, engineVersion=_client?.EngineVersion, connecting = _connecting, stopping = _stopping, requestActive = _request is not null, emptyQueryVerified = EmptyQueryVerified, userCatalogOpened = false, servicesOnly=_servicesOnly, galleryOpened=IsGallery&&!IsSingleFile, startupCatalogQueried=_startupCatalogQueried };
    private void Notify() => Changed?.Invoke();

    public void Connect() => ConnectCore(false,false);
    public void OpenServices()
    {
        if(IsSingleFile)return;
        if(_connecting&&_servicesOnly){_openGalleryAfterConnect=false;return;}
        if(Connected)return;
        ConnectCore(false,true);
    }
    public void OpenLibrary()
    {
        if(IsSingleFile&&!CanConnect)return;
        if(_connecting&&_servicesOnly){_openGalleryAfterConnect=true;return;}
        if(Connected&&!IsSingleFile){if(!IsGallery){IsGallery=true;_servicesOnly=false;Feedback="图库已打开，模型资料保持不变。";Notify();}return;}
        _singleFile=null;ConnectCore(true,false);
    }
    private void ConnectCore(bool gallery,bool servicesOnly=false)
    {
        if (!CanConnect) return;
        IsGallery=gallery;_servicesOnly=servicesOnly;_openGalleryAfterConnect=false;_startupCatalogQueried=false;EmptyQueryVerified=false;
        _connecting = true; State = "正在连接"; Feedback = servicesOnly?"正在启动本地模型与设置服务…":"正在启动本地图库服务…"; Notify();
        _connection = ConnectCoreAsync();
    }
    private async Task ConnectCoreAsync()
    {
        bool servicesOnly=_servicesOnly,gallery=IsGallery;string? singleFile=_singleFile;
        try
        {
            await Task.Run(async () =>
            {
                string parent=previewDirectory;
                if(singleFile is not null){parent=Path.Combine(previewDirectory,"single-view");Luma.Core.Management.PreviewProfile.Open(parent,false);}
                var selection=Luma.Core.Management.PreviewLibrary.Select(parent,singleFile is null&&!servicesOnly&&diagnostics);
                string data = selection.Directory;
                DataDirectory=data;
                if(singleFile is{} file)await EngineProcess.PrepareSingleAsync(Path.Combine(AppContext.BaseDirectory,"engine","Luma.Engine.exe"),data,file).ConfigureAwait(false);
                var client = new EngineClient(Path.Combine(AppContext.BaseDirectory, "engine", "Luma.Engine.exe"), data);
                _client = client;
                client.Disconnected += _ => _dispatcher.TryEnqueue(() =>
                {
                    if (!ReferenceEquals(_client, client) || _connecting || _stopping || _closing) return;
                    _stop = HandleDisconnectedAsync(client);
                });
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
                await _client.ConnectAsync(timeout.Token).ConfigureAwait(false);
                if(!_client.ManagementEnabled||!_client.FileOperationsEnabled)throw new EngineProtocolException("management_mismatch");
                if(!servicesOnly){
                    _startupCatalogQueried=true;
                    var page = await _client.QueryAsync(new(), token: timeout.Token).ConfigureAwait(false);
                    EmptyQueryVerified=page.Total==0&&page.Items.Length==0;
                    if (diagnostics&&!gallery && (page.Total != 0 || page.Items.Length != 0)) throw new EngineRpcException("unexpected_preview_data");
                }
            });
            if(_openGalleryAfterConnect){IsGallery=true;_servicesOnly=false;_openGalleryAfterConnect=false;}
            Started = true; State = "已连接"; Feedback = singleFile is not null?"单图查看 · 未加入主图库":_servicesOnly?"本地服务已就绪，模型管理可独立使用。":"图库服务已就绪。";
        }
        catch (Exception error)
        {
            State = "连接失败"; Feedback = Message(error);
            // Retain a child after failed handshake until it releases ownership.
            if (_client is not null) await Task.Run(async () => await _client.StopAsync().ConfigureAwait(false));
        }
        finally { _connecting = false; Notify(); }
    }
    public async void Probe()
    {
        if (!CanProbe) return;
        var request = new CancellationTokenSource(); _request = request;
        State = "请求进行中"; Feedback = "10 秒通信请求进行中，可以切换页面或取消。"; Notify();
        try
        {
            await Task.Run(async () => await _client!.CallAsync("diagnostics.wait", new { milliseconds = 10000, safeMilliseconds = 250 }, null, request.Token).ConfigureAwait(false));
            if (!_stopping && !_closing) { State = "已连接"; Feedback = "请求已完成。"; }
        }
        catch (OperationCanceledException) { if (!_stopping && !_closing) { State = "已连接"; Feedback = "请求已取消。"; } }
        catch (Exception error) { if (!_stopping && !_closing) { State = "连接异常"; Feedback = Message(error); } }
        finally { if (ReferenceEquals(_request, request)) _request = null; request.Dispose(); Notify(); }
    }
    private async Task HandleDisconnectedAsync(EngineClient client)
    {
        _stopping = true; _request?.Cancel(); State = "连接已断开";
        Feedback = "引擎连接已中断，正在等待安全退出…"; Notify();
        try { await Task.Run(async () => await client.StopAsync().ConfigureAwait(false)); }
        catch (Exception) { Feedback = "引擎通信失败，请稍后重试安全断开。"; }
        finally
        {
            _stopping = false;
            if (!client.IsAlive) Feedback = "引擎连接已中断，可以重新连接。";
            Notify();
        }
    }
    public void Cancel()
    {
        if (!CanCancel) return;
        _request!.Cancel(); Feedback = "已发送取消，等待引擎安全结束请求…"; Notify();
    }
    public Task StopAsync(bool closing = false)
    {
        _closing |= closing;
        if (_stop is not null && !_stop.IsCompleted) return _stop;
        _stop = StopCoreAsync(); return _stop;
    }
    private async Task StopCoreAsync()
    {
        _stopping = true; _request?.Cancel(); State = "正在安全断开"; Feedback = "正在等待请求结束并释放数据库。"; Notify();
        try
        {
            if (_connection is not null) await _connection;
            if (_client is not null)
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                try { await Task.Run(async () => await _client.StopAsync(timeout.Token).ConfigureAwait(false)); }
                catch (OperationCanceledException)
                {
                    Feedback = "引擎仍在安全退出，请稍候。"; Notify();
                    await Task.Run(async () => await _client.StopAsync().ConfigureAwait(false));
                }
            }
            State = "已断开"; Feedback = "引擎已退出，数据库已释放。";
        }
        catch (Exception error) { State = "断开未完成"; Feedback = Message(error); }
        finally { _stopping = false; Notify(); }
    }
    private static string Message(Exception error) => error switch
    {
        EngineRpcException e when e.Code == "engine_missing" => "缺少引擎文件，请重新解压完整预览包。",
        EngineRpcException e when e.Code == "engine_busy" => "测试库已由另一引擎使用。",
        EngineRpcException e when e.Code == "single_file_failed" => "这张图片无法打开：请检查格式、文件是否可读及当前解码预算。可重新选择图片。",
        EngineProtocolException => "引擎协议不兼容，请使用同一预览包。",
        OperationCanceledException => "连接超时，可重新连接。",
        _ => "引擎通信失败，请安全断开后重试。"
    };
}
