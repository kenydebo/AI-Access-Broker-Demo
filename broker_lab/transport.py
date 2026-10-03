"""Unix OS identity transport. Same UID means same principal, not a distinct agent."""
import ctypes
import ctypes.util
import json
import os
import socket
import stat
import sys
from .core import Caller, Denied

MAX_MESSAGE = 65536

class SocketUnavailable(Denied):
    pass

def check_socket_target(path):
    """Read-only preflight; never delete or connect to an existing socket."""
    if os.path.lexists(path):
        raise SocketUnavailable('Socket path already exists; no file removed. Stop the owning broker with Ctrl-C or choose a fresh --socket and use it for every client.')
    parent=os.path.dirname(os.path.abspath(path))
    if os.path.lexists(parent):
        info=os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise SocketUnavailable('Socket directory must be owned by this UID with mode 0700; no permissions changed.')


def peer_uid(connection):
    if hasattr(connection,'getpeereid'):
        return connection.getpeereid()[0]
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(ctypes.util.find_library('c'),use_errno=True)
        fn = libc.getpeereid
        fn.argtypes = [ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        uid,gid = ctypes.c_uint(),ctypes.c_uint()
        if fn(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid)) != 0:
            raise Denied('peer identity unavailable')
        return uid.value
    raise Denied('unsupported OS peer identity')

def receive(connection):
    data = bytearray()
    while b'\n' not in data:
        block = connection.recv(min(4096,MAX_MESSAGE+1-len(data)))
        if not block:
            raise Denied('incomplete request')
        data.extend(block)
        if len(data)>MAX_MESSAGE:
            raise Denied('request too large')
    line,extra = bytes(data).split(b'\n',1)
    if extra:
        raise Denied('one message per connection')
    try:
        def unique(pairs):
            result = {}
            for k,v in pairs:
                if k in result:
                    raise Denied('duplicate JSON key')
                result[k] = v
            return result
        return json.loads(line,object_pairs_hook=unique)
    except (ValueError,UnicodeError):
        raise Denied('invalid JSON')

def handle(connection, broker):
    connection.settimeout(5)
    try:
        caller = Caller('uid:'+str(peer_uid(connection)))
        message = receive(connection)
        if not isinstance(message,dict) or message.get('op') not in ('issue','redeem'):
            raise Denied('unknown operation')
        expected = {'op','authorization_details'} | ({'grant'} if message['op']=='redeem' else set())
        if set(message) != expected:
            raise Denied('unexpected message fields')
        if message['op']=='issue':
            value = broker.issue(caller,message['authorization_details'])
        else:
            value = broker.redeem(caller,message['grant'],message['authorization_details'])
        response = {'ok':True,'result':value}
    except Exception:
        # No grants, requests, tokens or upstream error bodies in logs or error replies.
        response = {'ok':False,'error':'denied'}
    connection.sendall(json.dumps(response).encode()+b'\n')

def serve(path, broker):
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.mkdir(parent,0o700)
    except FileExistsError:
        pass
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SocketUnavailable('socket directory must be owned by current UID with mode 0700')
    check_socket_target(path)
    listener = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    bound_identity=None
    try:
        listener.bind(path)
        bound_info=os.lstat(path)
        bound_identity=(bound_info.st_dev,bound_info.st_ino)
        os.chmod(path,0o600)
        listener.listen(16)
        print('Broker ready on private Unix socket; OS UID is the authenticated principal.',flush=True)
        while True:
            connection,_ = listener.accept()
            with connection:
                try:
                    handle(connection,broker)
                except (OSError,ValueError):
                    pass
    finally:
        listener.close()
        if bound_identity is not None:
            try:
                current=os.lstat(path)
                if stat.S_ISSOCK(current.st_mode) and current.st_uid==os.getuid() and (current.st_dev,current.st_ino)==bound_identity:
                    os.unlink(path)
            except FileNotFoundError:
                pass
