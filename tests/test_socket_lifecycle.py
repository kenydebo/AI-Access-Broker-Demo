"""Only disposable temp Unix sockets; never inspect or remove live lab sockets."""
import contextlib
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch,MagicMock
from broker_lab.transport import SocketUnavailable,check_socket_target,serve

class SocketLifecycleTests(unittest.TestCase):
    def test_existing_path_denied_without_delete_or_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'broker.sock')
            Path(path).write_text('existing unrelated file')
            with patch('broker_lab.transport.socket.socket') as factory:
                with self.assertRaises(SocketUnavailable):serve(path,object())
                factory.assert_not_called()
            self.assertEqual(Path(path).read_text(),'existing unrelated file')
    def test_ctrl_c_cleans_only_own_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'broker.sock')
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            wrapped=MagicMock(wraps=listener)
            wrapped.accept.side_effect=KeyboardInterrupt
            with patch('broker_lab.transport.socket.socket',return_value=wrapped),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):serve(path,object())
            self.assertFalse(os.path.lexists(path))
            self.assertEqual(listener.fileno(),-1)
    def test_failed_bind_never_unlinks_competing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'broker.sock')
            listener=MagicMock()
            def competing_bind(target):
                Path(target).write_text('created by another process')
                raise OSError('injected competing bind')
            listener.bind.side_effect=competing_bind
            with patch('broker_lab.transport.socket.socket',return_value=listener):
                with self.assertRaises(OSError):serve(path,object())
            self.assertEqual(Path(path).read_text(),'created by another process')
    def test_login_occupied_socket_fails_before_private_prompts(self):
        from broker_lab.user_login import main
        import sys
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'broker.sock');Path(path).write_text('existing')
            with patch.object(sys,'argv',['lab','--approved-setup','--executor','runtime','--socket',path]),patch('broker_lab.user_login.getpass.getpass') as prompt,patch('broker_lab.user_login.login') as login:
                with self.assertRaises(SocketUnavailable):main()
                prompt.assert_not_called();login.assert_not_called()
            self.assertTrue(Path(path).exists())
