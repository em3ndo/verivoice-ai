import asyncio
import json
import httpx
import ssl
import certifi
import websockets
from websockets.exceptions import InvalidStatus
from verivoice.config import Settings
from verivoice.providers.deepgram_api import DeepgramAPI

async def deepgram(settings):
    try:
        async with websockets.connect(DeepgramAPI.stream_url(), additional_headers={
            'Authorization': 'Token ' + settings.deepgram_api_key,
        }, ssl=ssl.create_default_context(cafile=certifi.where()), open_timeout=15) as socket:
            message = json.loads(await asyncio.wait_for(socket.recv(), 15))
            print('Deepgram handshake: accepted; initial event:', message.get('type', 'unknown'))
            await socket.send(json.dumps({'type': 'CloseStream'}))
    except InvalidStatus as error:
        print('Deepgram handshake: HTTP', error.response.status_code)
    except Exception as error:
        print('Deepgram handshake: failed (' + type(error).__name__ + ')')

async def hiya(settings, region):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f'https://api.hiya.com/audiointel/{region}/v1/user',
                headers={'Authorization': 'Bearer ' + settings.hiya_api_key, 'User-Agent': 'VeriVoice/0.1'})
            print('Hiya ' + region + ' credential check: HTTP', response.status_code)
    except Exception as error:
        print('Hiya ' + region + ' credential check: failed (' + type(error).__name__ + ')')

async def main():
    settings = Settings.from_env()
    await asyncio.gather(deepgram(settings), hiya(settings, 'us'), hiya(settings, 'eu'))

asyncio.run(main())
