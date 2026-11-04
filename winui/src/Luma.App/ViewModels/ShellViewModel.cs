using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace Luma.Preview.ViewModels;

internal enum WorkspacePage { Gallery, Models, Tags, Organize, Duplicates, Jobs, Storage, Settings }
internal enum PreviewState { Sample, Empty, Loading, Error }
internal sealed class WorkspaceState
{
    public string Query { get; set; } = "";
    public int Sort { get; set; }
    public int? SelectedId { get; set; }
    public PreviewState Mode { get; set; } = PreviewState.Sample;
    public string TagDraft { get; set; } = "";
}
internal sealed class ShellViewModel : INotifyPropertyChanged
{
    private WorkspacePage _page;
    public WorkspacePage Page { get => _page; set { if (_page == value) return; _page = value; Changed(); } }
    public Dictionary<WorkspacePage, WorkspaceState> Pages { get; } = Enum.GetValues<WorkspacePage>().ToDictionary(p => p, _ => new WorkspaceState());
    public WorkspaceState Current => Pages[Page];
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
