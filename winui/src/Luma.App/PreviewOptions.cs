namespace Luma.Preview;

internal sealed record PreviewOptions(string DataDirectory, bool Diagnostics, bool Capture, int TextScale, string Layout,bool ThumbnailPressure=false,bool MetadataFailureOnce=false)
{
    public string StorageRoot{get;init;}=Path.Combine(AppContext.BaseDirectory,"Data");
    public string? OpenFile{get;init;}
    public static PreviewOptions Parse(string[] arguments)
    {
        string? selected = null;
        string? openFile=null;
        bool diagnostics = false, capture = false;
        bool pressure=false,metadataFailure=false;
        int scale = 100;
        string layout = "Side";
        for (int i = 0; i < arguments.Length; i++)
        {
            switch (arguments[i])
            {
                case "--data-dir" when i + 1 < arguments.Length:
                    selected = arguments[++i];
                    break;
                case "--diagnostics": diagnostics = true; break;
                case "--capture": capture = true; break;
                case "--thumbnail-pressure": pressure=true;break;
                case "--metadata-failure-once": metadataFailure=true;break;
                case "--layout" when i + 1 < arguments.Length: layout = arguments[++i]; break;
                case "--text-scale" when i + 1 < arguments.Length && int.TryParse(arguments[++i], out int value):
                    scale = value;
                    break;
                case "--open-file" when i+1<arguments.Length && openFile is null:openFile=Path.GetFullPath(arguments[++i]);break;
                default: if(!arguments[i].StartsWith('-')&&openFile is null)openFile=Path.GetFullPath(arguments[i]);else throw new ArgumentException("unsupported argument");break;
            }
        }
        if ((pressure||metadataFailure)&&!diagnostics)throw new ArgumentException("diagnostics required");
        if (scale is not (100 or 125 or 150)) throw new ArgumentException("unsupported text scale");
        if (layout is not ("Side" or "Top") || (layout == "Top" && !diagnostics)) throw new ArgumentException("unsupported preview layout");
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string storageRoot=selected is null?Path.Combine(AppContext.BaseDirectory,"Data"):Path.TrimEndingDirectorySeparator(Path.GetFullPath(selected))+"-storage";
        string directory = Path.TrimEndingDirectorySeparator(Path.GetFullPath(selected ?? Luma.Core.Storage.StorageLayout.ResolveProfile(AppContext.BaseDirectory)));
        if(selected is not null&&!diagnostics&&!capture)directory=Luma.Core.Storage.StorageLayout.ResolveDataProfile(storageRoot,directory);
        string legacy = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.Combine(local, "Luma")));
        if (!Path.IsPathFullyQualified(directory) || directory.StartsWith(@"\\", StringComparison.Ordinal)
            || directory.Equals(legacy, StringComparison.OrdinalIgnoreCase)
            || directory.StartsWith(legacy + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("unsafe preview data directory");
        RejectReparsePoints(directory);
        // Diagnostics must never reuse a folder containing someone else's files.
        if ((diagnostics || capture) && Directory.Exists(directory))
            throw new ArgumentException("diagnostic output requires a fresh directory");
        Luma.Core.Management.PreviewProfile.Open(directory,diagnostics||capture);
        RejectReparsePoints(directory);
        return new PreviewOptions(directory, diagnostics, capture, scale, layout,pressure,metadataFailure)
        {StorageRoot=storageRoot,OpenFile=openFile};
    }

    public static void RejectReparsePoints(string path)
    {
        for (string? current = path; current is not null; current = Path.GetDirectoryName(current))
        {
            if ((File.Exists(current) || Directory.Exists(current))
                && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException("reparse points are not accepted in preview data paths");
        }
    }
}
