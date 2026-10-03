"""Unix boundary used by the separate real MCP stdio server.
The boundary itself is JSON request/reply, not the MCP protocol. No Salesforce credentials.
"""
import json
import socket
from .core import Denied
from .transport import receive

class BrokerBoundary:
    def __init__(self, socket_path):
        self.socket_path = socket_path
    def _call(self, message):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(self.socket_path)
            connection.sendall(json.dumps(message).encode()+b'\n')
            response = receive(connection)
        if response.get('ok') is not True:
            raise Denied('broker denied')
        return response['result']
    def authorize_read(self, authorization_details):
        return self._call({'op':'issue','authorization_details':authorization_details})
    def redeem_read(self, grant, authorization_details):
        return self._call({'op':'redeem','grant':grant,'authorization_details':authorization_details})
