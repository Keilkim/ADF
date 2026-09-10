"""Exercise the real subprocess boundary and transactional export publication."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import pymupdf

from adf.app import _kill_worker_process_tree, _publish_worker_outputs, _restrict_worker_directory
from adf.document import PdfDocument


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='adf-worker-test-')
        self.root = Path(self.folder.name)
        self.output = self.root/'output'
        self.output.mkdir()
        self.source = self.root/'source.pdf'
        with pymupdf.open() as document:
            for label in ('First','Second','Third'):
                document.new_page(width=300,height=400).insert_text((30,50),label)
            document.save(self.source)
        self.command = [sys.executable,str(Path(__file__).resolve().parents[1]/'main.py'),'--worker']

    def tearDown(self):
        self.folder.cleanup()

    def encrypted(self,user='reader',permissions=pymupdf.PDF_PERM_PRINT):
        encrypted = self.root/'protected.pdf'
        with pymupdf.open(self.source) as document:
            document.save(encrypted,encryption=pymupdf.PDF_ENCRYPT_AES_256,
                user_pw=user,owner_pw='owner-secret',permissions=permissions)
        return encrypted

    def request(self,task):
        job = Path(tempfile.mkdtemp(prefix='job-',dir=self.root))
        _restrict_worker_directory(job)
        request = job/'task.json'
        task = dict(task,result=str(job/'result.json'))
        request.write_text(json.dumps(task),encoding='utf-8')
        return request

    def run_worker(self,task):
        request = self.request(task)
        process = subprocess.run(self.command+[str(request)],capture_output=True,text=True,timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        payload = json.loads((request.parent/'result.json').read_text(encoding='utf-8'))
        return process.returncode,payload,request

    def assert_password_preserved(self,path):
        with pymupdf.open(path) as document:
            self.assertTrue(document.needs_pass)
            self.assertTrue(document.authenticate('reader'))
            self.assertFalse(document.permissions & pymupdf.PDF_PERM_MODIFY)

    def test_owner_snapshot_split_and_compression_preserve_encryption_across_process(self):
        source = self.encrypted()
        original = source.read_bytes()
        with PdfDocument() as model:
            model.open(source,'owner-secret')
            model.rotate([0],90)
            snapshot = self.root/'snapshot.pdf'
            snapshot.write_bytes(model.tobytes())
        with pymupdf.open(snapshot) as document:
            self.assertTrue(document.needs_pass)
        for operation in ('split','compress'):
            task = {'operation':operation,'source':str(snapshot),'source_password':'owner-secret'}
            if operation=='split':
                task.update(groups=[[0],[1,2]],output_dir=str(self.output),stem='split')
            else:
                task.update(output=str(self.output/'compressed.pdf'),options={'mode':'images'},
                    original_size=len(original))
            code,payload,_ = self.run_worker(task)
            self.assertEqual(code,0,payload)
            paths = payload['result'] if operation=='split' else [payload['result']['path']]
            for path in paths:
                self.assert_password_preserved(path)
            if operation=='compress':
                self.assertEqual(payload['result']['original_size'],len(original))
        self.assertEqual(source.read_bytes(),original)

    def test_user_password_cannot_bypass_owner_restrictions_in_worker(self):
        source = self.encrypted()
        for operation in ('split','compress'):
            task = {'operation':operation,'source':str(source),'source_password':'reader'}
            if operation=='split':
                task.update(groups=[[0]],output_dir=str(self.output),stem='blocked')
            else:
                task.update(output=str(self.output/'blocked.pdf'),options={'mode':'images'})
            code,payload,_ = self.run_worker(task)
            self.assertEqual(code,1)
            self.assertIn('제한',payload['error'])
        self.assertEqual(list(self.output.iterdir()),[])

    def test_empty_open_password_still_enforces_restrictions(self):
        source = self.encrypted(user='')
        code,payload,_ = self.run_worker({'operation':'split','source':str(source),
            'source_password':'','groups':[[0]],'output_dir':str(self.output),'stem':'blocked'})
        self.assertEqual(code,1)
        self.assertIn('제한',payload['error'])
        self.assertEqual(list(self.output.iterdir()),[])

    def test_extract_keeps_encryption_permissions_and_existing_files(self):
        source = self.encrypted()
        output = self.output/'selected.pdf'
        task = {'operation':'extract','source':str(source),'source_password':'reader',
                'pages':[0,2],'output':str(output)}
        code,payload,_ = self.run_worker(task)
        self.assertEqual(code,1)
        self.assertIn('제한',payload['error'])
        self.assertFalse(output.exists())
        task['source_password'] = 'owner-secret'
        code,payload,_ = self.run_worker(task)
        self.assertEqual(code,0,payload)
        self.assert_password_preserved(output)
        original = output.read_bytes()
        with pymupdf.open(output) as doc:
            doc.authenticate('reader')
            self.assertEqual([page.get_text().strip() for page in doc], ['First','Third'])
        code,payload,_ = self.run_worker(task)
        self.assertEqual(code,1)
        self.assertIn('이미 있습니다',payload['error'])
        self.assertEqual(output.read_bytes(),original)

    def test_merge_password_passes_process_boundary_and_outputs_have_correct_order(self):
        source = self.encrypted()
        task = {'operation':'merge','paths':[str(source),str(self.source)],
            'passwords':{str(source):'owner-secret'},'output':str(self.output/'merged.pdf')}
        code,payload,_ = self.run_worker(task)
        self.assertEqual(code,0,payload)
        with pymupdf.open(payload['result']) as document:
            self.assertEqual(document.page_count,6)
            self.assertEqual(document[3].get_text().strip(),'First')
        self.assertNotIn('owner-secret',json.dumps(payload))

    def test_completed_staging_is_not_visible_until_parent_publishes(self):
        code,payload,request = self.run_worker({'operation':'split','source':str(self.source),
            'groups':[[0],[1,2]],'output_dir':str(self.output),'stem':'staged','defer_publish':True})
        self.assertEqual(code,0,payload)
        self.assertEqual(list(self.output.iterdir()),[])
        self.assertEqual(len(payload['pending_outputs']),2)
        saved = _publish_worker_outputs(payload['pending_outputs'],request.parent)
        self.assertEqual(saved,payload['result'])
        self.assertEqual(len(list(self.output.glob('*.pdf'))),2)

    def test_cancelled_subprocess_cannot_leave_partial_split_outputs(self):
        source = self.root/'many.pdf'
        with pymupdf.open() as document:
            for page in range(140):
                document.new_page(width=300,height=400).insert_text((30,50),f'Page {page}')
            document.save(source)
        request = self.request({'operation':'split','source':str(source),
            'groups':[[i] for i in range(140)],'output_dir':str(self.output),'stem':'cancelled',
            'defer_publish':True})
        process = subprocess.Popen(self.command+[str(request)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline = time.monotonic()+20
            while time.monotonic()<deadline and process.poll() is None:
                if list(request.parent.glob('exports-*/*.pdf')):
                    break
                time.sleep(.01)
            self.assertTrue(list(request.parent.glob('exports-*/*.pdf')),'Worker did not start staging PDFs')
            _kill_worker_process_tree(process.pid)
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                _kill_worker_process_tree(process.pid)
                process.wait(timeout=10)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_failed_publication_rolls_back_batch_without_overwriting_existing_files(self):
        code,payload,request = self.run_worker({'operation':'split','source':str(self.source),
            'groups':[[0],[1]],'output_dir':str(self.output),'stem':'batch','defer_publish':True})
        self.assertEqual(code,0,payload)
        actual_link = os.link
        calls = 0
        def fail_second(source,target):
            nonlocal calls
            calls += 1
            if calls==2:
                raise OSError('simulated destination error')
            return actual_link(source,target)
        with patch('adf.app.os.link',side_effect=fail_second):
            with self.assertRaises(OSError):
                _publish_worker_outputs(payload['pending_outputs'],request.parent)
        self.assertEqual(list(self.output.iterdir()),[])
        existing = Path(payload['result'][1])
        existing.write_bytes(b'previous file')
        with self.assertRaises(FileExistsError):
            _publish_worker_outputs(payload['pending_outputs'],request.parent)
        self.assertEqual(existing.read_bytes(),b'previous file')
        self.assertFalse(Path(payload['result'][0]).exists())

    def test_existing_export_destination_is_rejected_before_work(self):
        output = self.output/'existing.pdf'
        output.write_bytes(b'keep this')
        code,payload,_ = self.run_worker({'operation':'compress','source':str(self.source),
            'output':str(output),'options':{'mode':'images'}})
        self.assertEqual(code,1)
        self.assertIn('이미 있습니다',payload['error'])
        self.assertEqual(output.read_bytes(),b'keep this')


if __name__=='__main__':
    unittest.main()
