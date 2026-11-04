using Luma.Core.Management;
using Luma.Core.Storage;
using System.Text.Json;

static class StorageTests
{
    public static int Run()
    {
        int passed=0;
        void Check(bool condition,string name){if(!condition)throw new Exception("storage: "+name);passed++;}
        void Reject(Action action,string name){try{action();}catch(IOException){passed++;return;}catch(ArgumentException){passed++;return;}throw new Exception("storage accepted "+name);}
        string root=Path.Combine(AppContext.BaseDirectory,"storage-lab-"+Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string app=Path.Combine(root,"应用程序");Directory.CreateDirectory(app);
            string data=Path.Combine(app,"Data"),profile=StorageLayout.ResolveProfile(app);
            Check(profile==Path.Combine(data,"Profiles","default"),"app-local default");
            Check(!Directory.Exists(data),"resolution has no writes");
            PreviewProfile.Open(profile,false);
            var library=PreviewLibrary.Select(profile,false);
            File.WriteAllBytes(Path.Combine(library.Directory,"library.sqlite3"),new byte[]{83,81,76,0,1,2,3});
            File.WriteAllBytes(Path.Combine(library.Directory,"library.sqlite3-wal"),new byte[]{4,5,6});
            File.WriteAllBytes(Path.Combine(library.Directory,"management-operations.sqlite3"),new byte[]{7,8,9});
            File.WriteAllText(Path.Combine(profile,"winui-window-v1.json"),"{}");
            Directory.CreateDirectory(Path.Combine(library.Directory,"thumbnails"));
            File.WriteAllText(Path.Combine(library.Directory,"thumbnails","private-do-not-copy.jpg"),"synthetic cache");
            var plan=ProfileSnapshots.Preview(profile);
            Check(plan.Files.Count==7,"all essential files including WAL and receipts");
            Check(plan.Files.All(f=>!f.RelativePath.Contains("thumbnails")),"cache excluded");
            string backup=Path.Combine(data,"Backups","one");
            using(var busy=new FileStream(Path.Combine(library.Directory,"library.sqlite3"),FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite))
                Reject(()=>ProfileSnapshots.Create(plan,backup),"busy database");
            Check(!Directory.Exists(backup),"busy failure does not publish backup");
            string saved=ProfileSnapshots.Create(plan,backup);
            File.WriteAllText(Path.Combine(library.Directory,"file-reviews.sqlite3"),"synthetic receipt added after preview");
            Reject(()=>ProfileSnapshots.Create(plan,Path.Combine(data,"Backups","missing-receipt")),"new receipt omitted");
            File.Delete(Path.Combine(library.Directory,"file-reviews.sqlite3"));
            Check(saved==backup&&ProfileSnapshots.Verify(saved).Files.Count==7,"published verified snapshot");
            Check(File.ReadAllBytes(Path.Combine(library.Directory,"library.sqlite3-wal")).SequenceEqual(new byte[]{4,5,6}),"source WAL untouched");
            string restored=ProfileSnapshots.Restore(saved,data);
            Check(StorageLayout.ResolveProfile(app)==restored,"fresh profile activated");
            Check(StorageLayout.ResolveDataProfile(data,profile)==restored,"explicit profile follows activation");
            Check(restored!=profile&&File.Exists(Path.Combine(profile,"winui-window-v1.json")),"restore preserves current profile");
            string relative=Path.GetRelativePath(profile,Path.Combine(library.Directory,"library.sqlite3-wal"));
            Check(File.ReadAllBytes(Path.Combine(restored,relative)).SequenceEqual(new byte[]{4,5,6}),"WAL preserved on restore");
            string pointer=File.ReadAllText(Path.Combine(data,"active-profile.json"));
            File.AppendAllText(Path.Combine(saved,relative),"tamper");
            Reject(()=>ProfileSnapshots.Restore(saved,data),"tampered payload");
            Check(File.ReadAllText(Path.Combine(data,"active-profile.json"))==pointer,"failed restore leaves active pointer");
            Reject(()=>ProfileSnapshots.Create(plan,profile),"destination source overlap");
            File.AppendAllText(Path.Combine(profile,"winui-window-v1.json")," ");
            Reject(()=>ProfileSnapshots.Create(plan,Path.Combine(data,"Backups","stale")),"preview source changed");
            File.WriteAllText(Path.Combine(data,"active-profile.json"),JsonSerializer.Serialize(new{schema=1,directory="../../outside"}));
            Reject(()=>StorageLayout.ResolveProfile(app),"active pointer traversal");
            File.WriteAllText(Path.Combine(data,"active-profile.json"),"not-json");
            Reject(()=>StorageLayout.ResolveProfile(app),"corrupt active pointer");
            using var cancel=new CancellationTokenSource();cancel.Cancel();
            try{ProfileSnapshots.Create(ProfileSnapshots.Preview(profile),Path.Combine(data,"Backups","cancelled"),cancel.Token);throw new Exception("cancellation ignored");}catch(OperationCanceledException){passed++;}
            Check(!Directory.Exists(Path.Combine(data,"Backups","cancelled")),"cancel does not publish");
            return passed;
        }
        finally{Directory.Delete(root,true);}
    }
}
