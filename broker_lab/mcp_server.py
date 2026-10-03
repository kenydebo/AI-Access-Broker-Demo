"""Actual MCP stdio adapter. No Salesforce credential access or identity overrides."""
import argparse
import asyncio
import json
from pathlib import Path
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from .boundary import BrokerBoundary
from .core import Denied


def make_server(socket_path):
    boundary = BrokerBoundary(socket_path)
    details_schema = json.loads((Path(__file__).resolve().parent.parent/'request.schema.json').read_text())
    schemas = {}
    for name in ('authorize_read','redeem_read'):
        properties = {'authorization_details':details_schema}
        if name=='redeem_read':
            properties['grant'] = {'type':'string'}
        schemas[name] = {'type':'object','additionalProperties':False,
                         'required':list(properties),'properties':properties}

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[
            types.Tool(name=name,description=(
                'Request an exact allowlisted Opportunity read grant.' if name=='authorize_read'
                else 'Redeem a grant once; broker rechecks policy and request binding.'),
                input_schema=schema) for name,schema in schemas.items()])

    async def call_tool(context, params):
        try:
            args = params.arguments
            if params.name not in schemas or not isinstance(args,dict) or set(args)!=set(schemas[params.name]['required']):
                raise Denied('unknown tool or arguments')
            if params.name=='authorize_read':
                value = boundary.authorize_read(args['authorization_details'])
            else:
                value = boundary.redeem_read(args['grant'],args['authorization_details'])
            return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(value))],
                                        structured_content=value,is_error=False)
        except (Denied,OSError,ValueError,KeyError,TypeError):
            return types.CallToolResult(content=[types.TextContent(type='text',text='broker denied or unavailable')],
                                        is_error=True)

    return Server('AI Access Broker Lab',version='0.1.0',on_list_tools=list_tools,on_call_tool=call_tool)


async def run(socket_path):
    server = make_server(socket_path)
    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description='MCP stdio adapter; separate broker owns authentication and credentials')
    parser.add_argument('--socket',default='.run/broker.sock')
    args = parser.parse_args()
    asyncio.run(run(args.socket))


if __name__=='__main__':
    main()
