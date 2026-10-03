"""Small SDK client for the local MCP vertical slice; no model or credentials."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters, stdio_client
from .__main__ import details
from .core import Denied


def value(result):
    if result.is_error:
        raise Denied('broker denied or unavailable')
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


async def read(socket_path, record):
    params = StdioServerParameters(command=sys.executable,
        args=['-m','broker_lab.mcp_server','--socket',socket_path],
        cwd=str(Path(__file__).resolve().parent.parent),env={'PYTHONDONTWRITEBYTECODE':'1'})
    output,failed = None,False
    async with stdio_client(params) as (incoming,outgoing):
        async with ClientSession(incoming,outgoing) as session:
            await session.initialize()
            try:
                tools = await session.list_tools()
                if {tool.name for tool in tools.tools} != {'authorize_read','redeem_read'}:
                    raise Denied('unexpected MCP tools')
                request = details(record)
                grant = value(await session.call_tool('authorize_read',{'authorization_details':request}))['grant']
                output = value(await session.call_tool('redeem_read',{'grant':grant,'authorization_details':request}))
            except Denied:
                failed = True
    if failed:
        raise Denied('broker denied or unavailable')
    return output


def main():
    parser = argparse.ArgumentParser(description='SDK stdio client -> local broker -> actual OPA -> mock Salesforce by default')
    parser.add_argument('--socket',default='.run/broker.sock')
    parser.add_argument('--record',choices=['A','B'],default='A')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(read(args.socket,args.record))))
    except Denied:
        raise SystemExit('Denied; no downstream data returned.')


if __name__=='__main__':
    main()
