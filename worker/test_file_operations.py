"""Stage7 uses only generated images, inert sidecars and independent marked stores."""
import hashlib,inspect,os,shutil,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from desktop.service import LibraryService
from worker.ownership import prepare_preview
from worker.dispatch import dispatch

class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='luma-stage7-')
        self.base=Path(self.temp.name);self.data=self.base/'data';prepare_preview(self.data)
        self.pictures=self.base/'synthetic-images';self.pictures.mkdir()
        for name,color in [('a','navy'),('c','orange')]:
            with Image.new('RGB',(32,32),color) as im:im.save(self.pictures/(name+'.png'))
        shutil.copyfile(self.pictures/'a.png',self.pictures/'b.png')
        self.originals={p.name:p.read_bytes() for p in self.pictures.iterdir()}
        self.service=LibraryService(self.data,automatic_recovery=False)
        self.wait(self.service.import_directory(str(self.pictures)))
        self.event=threading.Event()
    def tearDown(self):
        self.service.close();self.temp.cleanup()
    def call(self,method,payload):return dispatch(self.service,method,payload,self.event)
    def read(self,section,**args):return self.call('manage.read',{'section':section,'args':args})
    def write(self,action,**args):return self.call('manage.write',{'operationId':uuid.uuid4().hex,'action':action,'args':args})
    def wait(self,job,allow_errors=False):
        end=time.monotonic()+20
        while time.monotonic()<end:
            result=self.service.job(job['id'])
            if result['status'] not in ('queued','running'):
                self.assertIn(result['status'],('completed','completed_with_errors') if allow_errors else ('completed',),result)
                if not allow_errors:self.assertEqual(result['errors'],0)
                return result
            time.sleep(.02)
        self.fail('synthetic task timed out')
    def preview(self,kind,**options):
        result=self.write('files.preview',kind=kind,options=options);self.wait(result)
        review=self.read('files.review',reviewId=result['reviewId']);self.assertEqual(review['state'],'ready');return review
    def execute(self,review,**extra):return self.write('files.execute',reviewId=review['id'],fingerprint=review['fingerprint'],confirmed=True,**extra)
    def test_preview_has_no_effect_requires_confirmation_and_is_one_use(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        self.assertEqual(review['actionable'],3)
        items=self.read('files.items',reviewId=review['id']);self.assertEqual(len(items['items']),3)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
        with self.assertRaises(ValueError):self.write('files.execute',reviewId=review['id'],fingerprint=review['fingerprint'],confirmed=False)
        with self.assertRaises(ValueError):self.write('files.execute',reviewId=review['id'],fingerprint='0'*64,confirmed=True)
        job=self.execute(review);self.wait(job)
        for name,content in self.originals.items():
            self.assertFalse((self.pictures/name).exists());self.assertEqual((self.base/'sorted'/'PNG'/name).read_bytes(),content)
        with self.assertRaises(ValueError):self.execute(review)
        undo=self.preview('organize_undo',jobId=job['id']);self.wait(self.execute(undo))
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
    def test_catalog_change_invalidates_review_without_moving(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        row=self.call('catalog.query',{'limit':1})['items'][0]
        self.write('image.patch',imageId=row['id'],identity=row['identity'],favorite=True)
        with self.assertRaises(ValueError):self.execute(review)
        for name in self.originals:self.assertTrue((self.pictures/name).exists())
    def test_replaced_source_same_size_and_mtime_is_not_moved(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        path=self.pictures/'a.png';before=path.stat();replacement=self.base/'replacement.png';replacement.write_bytes(self.originals['a.png']);os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));os.replace(replacement,path)
        self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        self.assertEqual(path.read_bytes(),self.originals['a.png']);self.assertFalse((self.base/'sorted/PNG/a.png').exists())
    def test_conflicts_and_hardlinks_are_preserved(self):
        alias=self.base/'alias.png';os.link(self.pictures/'a.png',alias)
        target=self.base/'sorted/PNG';target.mkdir(parents=True);(target/'c.png').write_bytes(b'synthetic existing target')
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        self.assertGreater(review['skipped']+review['conflicts'],0);self.wait(self.execute(review),True)
        self.assertEqual(alias.read_bytes(),self.originals['a.png']);self.assertTrue((self.pictures/'a.png').exists());self.assertEqual((target/'c.png').read_bytes(),b'synthetic existing target')
    def test_ready_preview_survives_restart_without_executing(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        self.service.close();self.service=LibraryService(self.data,automatic_recovery=False)
        reopened=self.read('files.review',reviewId=review['id']);self.assertEqual(reopened['fingerprint'],review['fingerprint'])
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
    def test_duplicate_cleanup_restore_and_independent_permanent_preview(self):
        with patch('PIL.Image.open',side_effect=AssertionError('no pixel decoding')):
            self.wait(self.write('files.scan'))
            scan=self.read('files.state')['scan'];groups=self.read('files.duplicate.groups',scanId=scan['id'])['items'];self.assertEqual(len(groups),1)
            group=groups[0];members=self.read('files.duplicate.members',groupId=group['id'])['items']
            keeper=next(m for m in members if m['name']=='b.png')
            self.write('files.duplicate.keep',groupId=group['id'],imageId=keeper['id'])
            self.write('files.duplicate.select',groupId=group['id'],selected=True)
            review=self.preview('duplicate_cleanup',scanId=scan['id']);job=self.execute(review);self.wait(job)
            self.assertEqual((self.pictures/'b.png').read_bytes(),self.originals['b.png']);self.assertFalse((self.pictures/'a.png').exists())
            restore=self.preview('duplicate_undo',jobId=job['id']);self.wait(self.execute(restore))
            self.assertEqual((self.pictures/'a.png').read_bytes(),self.originals['a.png'])
    def test_remove_root_retains_overlapping_index_and_all_files(self):
        roots=self.service.roots();original=roots[0]['id'];parent=self.service.store.add_root(self.base)
        for image in self.call('catalog.query',{'limit':120})['items']:
            p=Path(image['path']);self.service.store.upsert(p,parent,p.stat(),{'width':32,'height':32,'format':'PNG'})
        review=self.preview('root_remove',rootId=original)
        self.wait(self.execute(review));self.assertEqual(len(self.service.roots()),1)
        self.assertEqual(self.call('catalog.query',{'limit':120})['total'],3)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
    def test_purge_is_separate_and_restore_never_overwrites(self):
        self.wait(self.write('files.scan'));scan=self.read('files.state')['scan'];group=self.read('files.duplicate.groups',scanId=scan['id'])['items'][0]
        members=self.read('files.duplicate.members',groupId=group['id'])['items'];keep=next(r for r in members if r['name']=='b.png');self.write('files.duplicate.keep',groupId=group['id'],imageId=keep['id']);self.write('files.duplicate.select',groupId=group['id'],selected=True)
        cleanup=self.preview('duplicate_cleanup',scanId=scan['id']);item=self.read('files.items',reviewId=cleanup['id'])['items'][0]
        job=self.execute(cleanup);self.wait(job);quarantine=Path(item['target']);self.assertTrue(quarantine.is_file())
        (self.pictures/'a.png').write_bytes(b'synthetic occupied original path')
        restore=self.preview('duplicate_undo',jobId=job['id']);self.assertGreater(self.wait(self.execute(restore),True)['errors'],0)
        self.assertEqual((self.pictures/'a.png').read_bytes(),b'synthetic occupied original path');self.assertTrue(quarantine.exists())
        purge=self.preview('duplicate_purge',jobId=job['id'])
        with self.assertRaises(ValueError):self.execute(purge)
        self.wait(self.execute(purge,confirmation='永久清空'));self.assertFalse(quarantine.exists());self.assertEqual((self.pictures/'b.png').read_bytes(),self.originals['b.png'])
    def test_rules_compare_and_swap_and_preview_invalidated(self):
        state=self.read('files.state');rules=[{'id':'synthetic-rule','name':'合成PNG','match':'PNG','kind':'format','destination':'','enabled':True}]
        with self.assertRaises(ValueError):self.write('files.rules.save',rules=rules,expectedHash='0'*64)
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        self.write('files.rules.save',rules=rules,expectedHash=state['rulesHash'])
        with self.assertRaises(ValueError):self.execute(review)
    def test_startup_never_recovers_originals_without_user_preview(self):
        self.assertIn('automatic_recovery',inspect.signature(LibraryService).parameters)
        self.service.close()
        with self.service.store.connect() as db:
            db.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES('synthetic-interrupted','organize','interrupted',0,0)")
            db.execute("INSERT INTO journal(job_id,image_id,source,destination,temp_path,size,mtime_ns,state,created_at,updated_at) VALUES('synthetic-interrupted',1,?,?,?,1,0,'prepared',0,0)",(str(self.pictures/'a.png'),str(self.base/'target.png'),str(self.base/'temp.part')))
        with patch('backend.organizer.Organizer.recover',side_effect=AssertionError('must not auto-recover')):
            self.service=LibraryService(self.data,automatic_recovery=False)
            self.assertFalse(self.service.jobs() and any(j['kind']=='recovery' for j in self.service.jobs()))
            self.assertGreater(self.read('files.state')['recovery']['moves'],0)
            with self.assertRaises(ValueError):self.write('files.scan')
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)

    def test_published_target_recovers_only_after_a_separate_confirmed_review(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        # Simulate catalog failure after the safe copy and source deletion.
        with patch.object(self.service.organizer,'_catalog_move',side_effect=OSError('synthetic catalog failure')):
            self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        self.assertGreater(self.read('files.state')['recovery']['moves'],0)
        recovery=self.preview('recover_moves')
        self.assertTrue(all((self.base/'sorted'/'PNG'/name).read_bytes()==content for name,content in self.originals.items()))
        with self.assertRaises(ValueError):self.write('files.execute',reviewId=recovery['id'],fingerprint=recovery['fingerprint'],confirmed=False)
        self.wait(self.execute(recovery));self.assertEqual(self.read('files.state')['recovery']['moves'],0)
        self.assertTrue(all(Path(row['path']).is_file() for row in self.call('catalog.query',{'limit':120})['items']))

    def test_recovery_target_replacement_preserves_the_source_and_changed_target(self):
        from backend.duplicates import VerifiedFile
        original_delete=VerifiedFile.delete
        def fail_delete(file):
            if file.path==self.pictures/'a.png':raise OSError('synthetic delete failure')
            return original_delete(file)
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        with patch.object(VerifiedFile,'delete',fail_delete):self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        recovery=self.preview('recover_moves');target=self.base/'sorted'/'PNG'/'a.png'
        replacement=self.base/'replacement.png';replacement.write_bytes(self.originals['a.png']);os.replace(replacement,target)
        self.assertGreater(self.wait(self.execute(recovery),True)['errors'],0)
        self.assertEqual((self.pictures/'a.png').read_bytes(),self.originals['a.png']);self.assertEqual(target.read_bytes(),self.originals['a.png'])

    def test_disk_full_during_verified_copy_keeps_all_originals(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        with patch('backend.organizer.os.fsync',side_effect=OSError(28,'synthetic disk full')):
            self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
        self.assertFalse(any((self.base/'sorted').rglob('*.png')))

    def test_busy_source_handle_is_skipped_without_overwrite(self):
        from backend.duplicates import VerifiedFile
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        with VerifiedFile(self.pictures/'a.png'):
            self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        self.assertEqual((self.pictures/'a.png').read_bytes(),self.originals['a.png'])
        self.assertFalse((self.base/'sorted'/'PNG'/'a.png').exists())

    def test_operation_receipt_reconnect_does_not_replay_execute(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        operation=uuid.uuid4().hex;args={'reviewId':review['id'],'fingerprint':review['fingerprint'],'confirmed':True}
        first=self.call('manage.write',{'operationId':operation,'action':'files.execute','args':args});self.wait(first)
        self.service.close();self.service=LibraryService(self.data,automatic_recovery=False)
        status=self.call('manage.status',{'operationId':operation});self.assertIn(status['state'],('completed',))
        cached=self.call('manage.write',{'operationId':operation,'action':'files.execute','args':args});self.assertEqual(cached['id'],first['id'])
        with self.service.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE kind='organize'").fetchone()[0],1)

    def test_rules_single_change_preserves_unseen_pages(self):
        state=self.read('files.state');rules=[{'id':f'r{i}','name':f'synthetic{i}','match':f'{i}','kind':'format','destination':'','enabled':True} for i in range(67)]
        self.write('files.rules.save',rules=rules,expectedHash=state['rulesHash']);state=self.read('files.state')
        self.assertEqual(len(state['rules']),25);self.assertEqual(state['rulesTotal'],67)
        updated={**rules[0],'name':'changed'};self.write('files.rules.change',rule=updated,expectedHash=state['rulesHash'])
        self.assertEqual(self.service.rules()[66],rules[66]);self.assertEqual(len(self.service.rules()),67)

    def test_cancelled_preview_never_becomes_executable(self):
        from backend.organizer import Cancelled
        with patch.object(self.service.organizer,'preview',side_effect=Cancelled()):
            submitted=self.write('files.preview',kind='organize',options={'mode':'format','destination':str(self.base/'sorted'),'scope':'all'})
            end=time.monotonic()+10
            while self.service.job(submitted['id'])['status'] in ('queued','running') and time.monotonic()<end:time.sleep(.02)
        review=self.read('files.review',reviewId=submitted['reviewId']);self.assertNotEqual(review['state'],'ready')
        with self.assertRaises(ValueError):self.write('files.execute',reviewId=review['id'],fingerprint='0'*64,confirmed=True)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)

    def test_real_second_volume_copy_and_undo_verify_all_bytes(self):
        target_base=Path(__file__).resolve().parents[1]/'build'/'winui-stage7'
        target_base.mkdir(exist_ok=True,parents=True)
        if self.pictures.stat().st_dev==target_base.stat().st_dev:self.skipTest('second physical volume unavailable')
        with tempfile.TemporaryDirectory(prefix='synthetic-cross-volume-',dir=target_base) as target:
            review=self.preview('organize',mode='format',destination=target,scope='all');job=self.execute(review);self.wait(job)
            for name,content in self.originals.items():
                self.assertFalse((self.pictures/name).exists());self.assertEqual((Path(target)/'PNG'/name).read_bytes(),content)
            undo=self.preview('organize_undo',jobId=job['id']);self.wait(self.execute(undo))
            for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)

    def test_cancel_after_publication_keeps_source_until_explicit_recovery(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        original_state=self.service.organizer._state
        def cancel_after_publish(ident,state,*args,**kwargs):
            result=original_state(ident,state,*args,**kwargs)
            if state=='published':
                with self.service.store.connect() as db:job_id=db.execute('SELECT job_id FROM journal WHERE id=?',(ident,)).fetchone()[0]
                self.service.manager.cancel(job_id)
            return result
        with patch.object(self.service.organizer,'_state',cancel_after_publish):
            task=self.execute(review);end=time.monotonic()+15
            while self.service.job(task['id'])['status'] in ('queued','running') and time.monotonic()<end:time.sleep(.02)
        self.assertEqual(self.service.job(task['id'])['status'],'cancelled')
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
        self.assertGreater(self.read('files.state')['recovery']['moves'],0)
        recovery=self.preview('recover_moves');self.wait(self.execute(recovery))
        self.assertEqual(self.read('files.state')['recovery']['moves'],0)

    def moved_bundle(self):
        side=self.pictures/'a.txt';side.write_bytes(b'synthetic sidecar fixture, no private metadata')
        self.wait(self.service.import_directory(str(self.pictures)))
        image=next(r for r in self.call('catalog.query',{'limit':120})['items'] if r['name']=='a.png')
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='selected',ids=[image['id']])
        task=self.execute(review);self.wait(task)
        return task,image,side

    def test_interrupted_undo_between_image_and_sidecar_is_previewed_and_recovered(self):
        import json
        task,image,side=self.moved_bundle();expected=b'synthetic sidecar fixture, no private metadata'
        with self.service.store.connect() as db:
            row=dict(db.execute("SELECT * FROM journal WHERE job_id=? AND asset_kind='image' AND state='committed'",(task['id'],)).fetchone())
            db.execute("INSERT INTO jobs(id,kind,status,payload,created_at,updated_at) VALUES('synthetic-undo-crash','undo','interrupted',?,0,0)",(json.dumps({'job_id':task['id']}),))
        target=Path(row['destination']);info=target.stat()
        self.service.organizer.move('synthetic-undo-crash',image['id'],target,self.pictures/'a.png',info.st_size,info.st_mtime_ns,restore_root=row['source_root_id'],undo_of=row['id'])
        self.service.close();self.service=LibraryService(self.data,automatic_recovery=False)
        self.assertGreater(self.read('files.state')['recovery']['moves'],0)
        review=self.preview('recover_moves');items=self.read('files.items',reviewId=review['id'])['items']
        self.assertTrue(any(r['target']==str(side) for r in items));self.wait(self.execute(review))
        self.assertEqual(side.read_bytes(),expected);self.assertEqual((self.pictures/'a.png').read_bytes(),self.originals['a.png'])
        self.assertEqual(self.read('files.state')['recovery']['moves'],0)

    def test_existing_changed_sidecar_target_never_reports_successful_recovery(self):
        task,image,side=self.moved_bundle()
        with self.service.store.connect() as db:
            plan_id=__import__('json').loads(db.execute('SELECT payload FROM jobs WHERE id=?',(task['id'],)).fetchone()[0])['plan_id']
            db.execute("UPDATE plan_items SET status='ready' WHERE plan_id=?",(plan_id,))
        moved_side=self.base/'sorted'/'PNG'/'a.txt';moved_side.write_bytes(b'synthetic external edit')
        review=self.preview('recover_moves');result=self.wait(self.execute(review),True)
        self.assertGreater(result['errors'],0);self.assertGreater(self.read('files.state')['recovery']['moves'],0)
        self.assertEqual(moved_side.read_bytes(),b'synthetic external edit');self.assertFalse(side.exists())

    def test_cancel_before_recovery_handler_clears_recovering_without_false_guarantee(self):
        self.read('files.state');gate=threading.Event()
        blockers=[self.service.manager.executor.submit(gate.wait) for _ in range(2)]
        self.service.manager.recovering=True
        try:
            task=self.service.manager.submit('file_recovery',{'reviewId':'synthetic-never-entered'})
            self.service.manager.cancel(task['id'])
        finally:gate.set()
        end=time.monotonic()+10
        while self.service.job(task['id'])['status'] in ('queued','running') and time.monotonic()<end:time.sleep(.02)
        result=self.service.job(task['id']);self.assertEqual(result['status'],'cancelled')
        self.assertNotIn('原图未修改',result['message']);self.assertFalse(self.service.manager.recovering)
        for blocker in blockers:blocker.result(timeout=5)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)

    def test_cancel_after_one_recovery_commit_retains_remaining_pending(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        with patch.object(self.service.organizer,'_catalog_move',side_effect=OSError('synthetic catalog failure')):self.wait(self.execute(review),True)
        review=self.preview('recover_moves');original_state=self.service.organizer._state
        def cancel_after_commit(ident,state,*args,**kwargs):
            result=original_state(ident,state,*args,**kwargs)
            if state=='committed':
                with self.service.store.connect() as db:running=db.execute("SELECT id FROM jobs WHERE kind='file_recovery' AND status='running'").fetchone()
                if running:self.service.manager.cancel(running[0])
            return result
        with patch.object(self.service.organizer,'_state',cancel_after_commit):
            task=self.execute(review);end=time.monotonic()+10
            while self.service.job(task['id'])['status'] in ('queued','running') and time.monotonic()<end:time.sleep(.02)
        result=self.service.job(task['id']);self.assertEqual(result['status'],'cancelled');self.assertNotIn('原图未修改',result['message'])
        self.assertGreater(self.read('files.state')['recovery']['moves'],0);self.assertFalse(self.service.manager.recovering)
        for name,content in self.originals.items():self.assertEqual((self.base/'sorted'/'PNG'/name).read_bytes(),content)

    def test_compensation_refuses_replaced_destination_even_when_bytes_match(self):
        side=self.pictures/'a.txt';side.write_bytes(b'synthetic bundle')
        self.wait(self.service.import_directory(str(self.pictures)))
        image=next(r for r in self.call('catalog.query',{'limit':120})['items'] if r['name']=='a.png')
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='selected',ids=[image['id']])
        move=self.service.organizer.move;destination=self.base/'sorted'/'PNG'/'a.png';replacement_inode=[]
        def replace_then_fail(*args,**kwargs):
            if kwargs.get('asset_kind')=='sidecar':raise OSError('synthetic sidecar failure')
            result=move(*args,**kwargs)
            if Path(args[2])==self.pictures/'a.png':
                replacement=self.base/'replacement.png';replacement.write_bytes(self.originals['a.png']);os.replace(replacement,destination);replacement_inode.append(destination.stat().st_ino)
            return result
        with patch.object(self.service.organizer,'move',replace_then_fail):self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        self.assertFalse((self.pictures/'a.png').exists());self.assertEqual(destination.stat().st_ino,replacement_inode[0]);self.assertEqual(destination.read_bytes(),self.originals['a.png']);self.assertEqual(side.read_bytes(),b'synthetic bundle')

    def test_discarded_backend_plan_is_shown_stale_before_consuming_review(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all')
        self.write('files.root.discard',rootId=self.service.roots()[0]['id'],confirmed=True)
        self.assertEqual(self.read('files.review',reviewId=review['id'])['state'],'stale')
        with self.assertRaises(ValueError):self.execute(review)

    def test_bundle_compensation_restores_original_when_identity_is_unchanged(self):
        side=self.pictures/'a.txt';side.write_bytes(b'synthetic bundle')
        self.wait(self.service.import_directory(str(self.pictures)))
        image=next(r for r in self.call('catalog.query',{'limit':120})['items'] if r['name']=='a.png')
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='selected',ids=[image['id']])
        move=self.service.organizer.move
        def fail_side(*args,**kwargs):
            if kwargs.get('asset_kind')=='sidecar':raise OSError('synthetic sidecar failure')
            return move(*args,**kwargs)
        with patch.object(self.service.organizer,'move',fail_side):self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        self.assertEqual((self.pictures/'a.png').read_bytes(),self.originals['a.png']);self.assertEqual(side.read_bytes(),b'synthetic bundle')
        self.assertFalse((self.base/'sorted'/'PNG'/'a.png').exists())

    def test_completed_undo_does_not_permanently_block_index_removal(self):
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all');task=self.execute(review);self.wait(task)
        undo=self.preview('organize_undo',jobId=task['id']);self.wait(self.execute(undo))
        root=self.service.roots()[0]['id'];review=self.preview('root_remove',rootId=root)
        self.assertEqual(review['actionable'],1,review['details']);self.wait(self.execute(review));self.assertNotIn(root,[r['id'] for r in self.service.roots()]);self.assertEqual(self.call('catalog.query',{'limit':120})['total'],0)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)

    def test_import_is_blocked_while_a_move_has_not_written_its_first_journal(self):
        from worker.file_ops import get
        ops=get(self.service);entered=threading.Event();release=threading.Event();original=ops.organize
        def held(manager,job_id,payload):
            entered.set();release.wait(10);return original(manager,job_id,payload)
        self.service.manager.handlers['organize']=held
        review=self.preview('organize',mode='format',destination=str(self.base/'sorted'),scope='all');task=self.execute(review)
        empty=self.base/'synthetic-empty';empty.mkdir()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ValueError):self.service.import_directory(str(empty))
        finally:release.set()
        self.wait(task)
        for name,content in self.originals.items():self.assertEqual((self.base/'sorted'/'PNG'/name).read_bytes(),content)

    def test_target_volume_unavailable_before_copy_preserves_originals(self):
        import errno
        destination=self.base/'unavailable-volume';review=self.preview('organize',mode='format',destination=str(destination),scope='all');mkdir=os.mkdir
        def unavailable(path,*args,**kwargs):
            if Path(path)==destination or destination in Path(path).parents:raise OSError(errno.ENODEV,'synthetic unavailable volume')
            return mkdir(path,*args,**kwargs)
        with patch('backend.organizer.os.mkdir',unavailable):self.assertGreater(self.wait(self.execute(review),True)['errors'],0)
        for name,content in self.originals.items():self.assertEqual((self.pictures/name).read_bytes(),content)
        self.assertFalse(destination.exists())

    def test_control_database_connections_are_serialized_through_close(self):
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        from worker.file_ops import get
        ops=get(self.service);ops.state();mutex=threading.Lock();active=peak=0;connect=sqlite3.connect
        class Tracked(sqlite3.Connection):
            def __init__(connection,*args,**kwargs):
                nonlocal active,peak
                super().__init__(*args,**kwargs)
                with mutex:active+=1;peak=max(peak,active)
            def close(connection):
                nonlocal active
                try:super().close()
                finally:
                    with mutex:active-=1
        def tracked(path,*args,**kwargs):return connect(path,*args,**{**kwargs,'factory':Tracked}) if Path(path)==ops.path else connect(path,*args,**kwargs)
        def work(_):
            for _ in range(10):
                with ops.db() as db:db.execute('SELECT COUNT(*) FROM reviews').fetchone();time.sleep(.001)
        with patch('worker.file_ops.sqlite3.connect',tracked),ThreadPoolExecutor(max_workers=8) as executor:list(executor.map(work,range(8)))
        self.assertEqual(active,0);self.assertEqual(peak,1)
