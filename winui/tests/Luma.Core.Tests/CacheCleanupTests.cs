using Luma.Core.Management;
using Luma.Core.Storage;

static class CacheCleanupTests
{
    public static int Run()
    {
        int count=0;void Check(bool value){if(!value)throw new Exception("cache cleanup");count++;}
        string root=Path.Combine(AppContext.BaseDirectory,"cache-lab-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
        try
        {
            PreviewProfile.Open(root,false);string library=PreviewLibrary.Select(root,false).Directory;
            File.WriteAllText(Path.Combine(library,"library.sqlite3"),"untouched index");
            string cache=Path.Combine(library,"thumbnails","aa","bb");Directory.CreateDirectory(cache);
            string one=Path.Combine(cache,new string('a',64)+".jpg"),two=Path.Combine(cache,new string('b',64)+".jpg");
            File.WriteAllText(one,"123");File.WriteAllText(two,"4567");
            string unknown=Path.Combine(cache,"personal.jpg");File.WriteAllText(unknown,"preserve");
            var plan=CacheCleanup.Preview(root);Check(plan.Files.Count==2&&plan.Bytes==7);
            File.AppendAllText(two,"changed");var result=CacheCleanup.Apply(plan);
            Check(result.Removed==1&&result.Skipped==1&&result.Bytes==3);
            Check(!File.Exists(one)&&File.Exists(two)&&File.Exists(unknown));
            Check(File.ReadAllText(Path.Combine(library,"library.sqlite3"))=="untouched index");
            var next=CacheCleanup.Preview(root);Check(next.Files.Count==1);
            string review=Path.Combine(root,"review.json");string seal=CacheCleanup.SaveReview(review,next);
            Check(CacheCleanup.LoadReview(review,seal).Files.Count==1);
            File.AppendAllText(review," ");
            try{CacheCleanup.LoadReview(review,seal);throw new Exception("modified review accepted");}catch(IOException){count++;}
            var malicious=next with{Files=[new("../library.sqlite3",next.Files[0].Identity,1)]};
            try{CacheCleanup.Apply(malicious);throw new Exception("cache traversal");}catch(IOException){count++;}
            using(var busy=new FileStream(two,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
            {Check(CacheCleanup.Apply(next).Skipped==1);}
            string unrelated=Path.Combine(root,"third-party");Directory.CreateDirectory(Path.Combine(unrelated,"thumbnails"));
            File.WriteAllText(Path.Combine(unrelated,"library.sqlite3"),"third-party database");
            string canary=Path.Combine(unrelated,"thumbnails",new string('c',64)+".jpg");File.WriteAllText(canary,"irreplaceable canary");
            try{CacheCleanup.Preview(unrelated);throw new Exception("unowned folder accepted");}catch(IOException){count++;}
            var forged=next with{Library=unrelated};
            try{CacheCleanup.Apply(forged);throw new Exception("unowned review accepted");}catch(IOException){count++;}
            Check(File.Exists(canary));
            return count;
        }
        finally{Directory.Delete(root,true);}
    }
}
