using Luma.Core.Viewer;using Luma.Core.Gallery;using Luma.Core.Engine;
namespace Luma.Preview.Services;
// Explicit diagnostics-only fault: pixel and all subsequent metadata reads use the real engine.
internal sealed class MetadataFailureOnceApi(IViewerApi actual):IViewerApi
{
 private bool _failed;
 public Task<ViewerWindow> NeighborsAsync(GalleryItem item,Dictionary<string,object?> filters,CancellationToken token)=>actual.NeighborsAsync(item,filters,token);
 public Task<GalleryDetail> DetailAsync(GalleryItem item,CancellationToken token){if(!_failed){_failed=true;return Task.FromException<GalleryDetail>(new EngineRpcException("operation_failed"));}return actual.DetailAsync(item,token);}
}
