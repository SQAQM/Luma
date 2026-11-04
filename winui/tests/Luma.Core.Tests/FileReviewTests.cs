using System.Text.Json;
using Luma.Core.Management;
static class FileReviewTests
{
 public static int Run()
 {
  int count=0;void Check(bool value){if(!value)throw new Exception("file review invariant");count++;}
  var session=new FileReviewState();session.ChangeContext("root1:PNG:target1");long old=session.Version;
  var ready=JsonSerializer.SerializeToElement(new{id=new string('a',32),kind="organize",state="ready",fingerprint=new string('b',64),total=3,actionable=3,skipped=0,conflicts=0});
  session.Accept(ready,old);Check(!session.CanExecute);Check(session.GetType().GetProperty("CanConfirm")?.GetValue(session) is true);session.Approve(true);Check(session.CanExecute);
  session.ChangeContext("root1:PNG:target2");Check(!session.CanExecute&&session.Review is null);session.Accept(ready,old);Check(session.Review is null);
  session.Accept(ready,session.Version);session.Approve(true);var submission=session.BeginExecute();Check(submission.Id==new string('a',32));Check(!session.CanExecute);Check(session.GetType().GetProperty("CanConfirm")?.GetValue(session) is false);session.Approve(true);Check(!session.CanExecute);
  session.Accept(ready,session.Version);Check(!session.CanExecute);session.Approve(true);Check(session.CanExecute);session.Revoke();Check(!session.CanExecute);
  var stale=JsonSerializer.SerializeToElement(new{id=new string('a',32),kind="organize",state="stale",fingerprint=new string('b',64),total=3,actionable=3});session.Accept(stale,session.Version);session.Approve(true);Check(!session.CanExecute);
  var building=JsonSerializer.SerializeToElement(new{id=new string('a',32),kind="organize",state="building",total=0,actionable=0});session.Accept(building,session.Version);session.Approve(true);Check(!session.CanExecute);
  return count;
 }
}
