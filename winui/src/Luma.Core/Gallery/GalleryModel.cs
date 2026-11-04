using Luma.Core.Contracts;
using Luma.Core.Engine;
namespace Luma.Core.Gallery;

// Dispatcher-owned, bounded to one data page, one detail and 128 cursor anchors.
public sealed class GalleryModel(IGalleryApi api):IDisposable
{
    private readonly EngineContext _page=new(),_detail=new();
    private CancellationTokenSource? _pageCancel,_detailCancel;
    private readonly List<string?> _history=[null];
    private int _position,_basePage;private string? _next;private long? _tag,_root;private bool _disposed;
    public event Action? Changed;
    public IReadOnlyList<GalleryItem> Items{get;private set;}=[];
    public HashSet<long> SelectedIds{get;}=[];
    public GalleryDetail? Detail{get;private set;}
    public GalleryItem? Current{get;private set;}
    public string Query{get;private set;}="";
    public string Sort{get;private set;}="name";
    public string ModelFilter{get;private set;}="";
    public string FormatFilter{get;private set;}="";
    public string ArtistFilter{get;private set;}="";
    public string PromptTagFilter{get;private set;}="";
    public bool Active{get;private set;}=true;
    public bool Loading{get;private set;}
    public bool DetailLoading{get;private set;}
    public string Error{get;private set;}="";
    public string DetailError{get;private set;}="";
    public long Total{get;private set;}
    public bool NeedsRefresh{get;private set;}=true;
    public int CursorCount=>_history.Count;
    public int PageNumber=>_basePage+_position+1;
    public bool CanNext=>!Loading&&_next is not null;
    public bool CanPrevious=>!Loading&&_position>0;
    public long Generation=>_page.Current.Generation;
    private Dictionary<string,object?> Filters()=>new(){["q"]=Query,["sort"]=Sort,["content_tag"]=_tag,["root_id"]=_root,["model"]=ModelFilter,["format"]=FormatFilter,["artist"]=ArtistFilter,["tag"]=PromptTagFilter};
    public Dictionary<string,object?> ScopeFilters()=>Filters();
    public Task ChangeScopeAsync(string query,string sort,long? tag,long? root,string modelFilter="",string formatFilter="",string artistFilter="",string promptTagFilter="")
    {Query=query;Sort=sort;_tag=tag;_root=root;ModelFilter=modelFilter;FormatFilter=formatFilter;ArtistFilter=artistFilter;PromptTagFilter=promptTagFilter;return RefreshAsync();}
    public Task NextAsync(){if(!CanNext)return Task.CompletedTask;if(_position+1<_history.Count)_history.RemoveRange(_position+1,_history.Count-_position-1);_history.Add(_next);if(_history.Count>128){_history.RemoveAt(0);_basePage++;}else _position++;return LoadAsync();}
    public Task PreviousAsync(){if(!CanPrevious)return Task.CompletedTask;_position--;return LoadAsync();}
    public Task RefreshAsync(){_history.Clear();_history.Add(null);_position=_basePage=0;return LoadAsync();}
    private async Task LoadAsync()
    {
        if(!Active||_disposed)return;
        _pageCancel?.Cancel();_pageCancel?.Dispose();var cancel=new CancellationTokenSource();_pageCancel=cancel;
        var stamp=_page.Advance("page");_detailCancel?.Cancel();_detail.Invalidate();NeedsRefresh=true;
        Items=[];SelectedIds.Clear();Detail=null;Current=null;DetailLoading=false;Error="";Loading=true;_next=null;Changed?.Invoke();
        try
        {
            var page=await api.QueryAsync(new(Filters(),_history[_position],120),_page,cancel.Token);
            if(!Active||_disposed||!_page.IsCurrent(stamp))return;
            Items=page.Items;Total=page.Total;_next=page.NextCursor;NeedsRefresh=false;
        }
        catch(Exception e){if(_page.IsCurrent(stamp)&&Active&&!_disposed)Error=Code(e);}
        finally{if(_page.IsCurrent(stamp)&&Active&&!_disposed){Loading=false;Changed?.Invoke();}}
    }
    public void SetSelection(IEnumerable<long> ids){SelectedIds.Clear();foreach(long id in ids.Take(120))if(Items.Any(i=>i.Id==id))SelectedIds.Add(id);Changed?.Invoke();}
    public async Task SelectAsync(GalleryItem? item)
    {
        if(!Active||_disposed)return;
        if(item is not null&&!Items.Any(i=>i.Id==item.Id&&i.Identity==item.Identity))item=null;
        _detailCancel?.Cancel();_detailCancel?.Dispose();var cancel=new CancellationTokenSource();_detailCancel=cancel;
        var stamp=_detail.Advance(item?.Identity??"");Current=item;Detail=null;DetailError="";DetailLoading=item is not null;Changed?.Invoke();
        if(item is null)return;
        try{var value=await api.DetailAsync(item,_detail,cancel.Token);if(Active&&!_disposed&&_detail.IsCurrent(stamp))Detail=value;}
        catch(Exception e){if(Active&&!_disposed&&_detail.IsCurrent(stamp))DetailError=Code(e);}
        finally{if(Active&&!_disposed&&_detail.IsCurrent(stamp)){DetailLoading=false;Changed?.Invoke();}}
    }
    public void Deactivate(){Active=false;_pageCancel?.Cancel();_detailCancel?.Cancel();_page.Invalidate();_detail.Invalidate();Loading=false;DetailLoading=false;}
    public void Cancel(){if(!Loading)return;_pageCancel?.Cancel();_page.Invalidate();Loading=false;Error="cancelled";NeedsRefresh=true;Changed?.Invoke();}
    public void Activate(){if(!_disposed)Active=true;}
    public static string Code(Exception e)=>e is EngineRpcException r?r.Code:e is OperationCanceledException?"cancelled":"operation_failed";
    public void Dispose(){_disposed=true;Deactivate();_pageCancel?.Dispose();_detailCancel?.Dispose();}
}
