"""Exercise actual async SDK requests through a mocked HTTP transport."""

import asyncio
import json
import os
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from google import genai
import httpx

from backend.services import assistant_service as assistant


class AssistantKeyRotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []
        self.requests = []
        self.rate_limited = False
        real_client = genai.Client

        async def handle(request):
            self.requests.append((request.headers['x-goog-api-key'], json.loads(request.content)))
            await asyncio.sleep(0)  # Allow requests using different keys to overlap.
            if self.rate_limited:
                return httpx.Response(429, headers={'retry-after-ms': '1'}, json={
                    'type': 'error',
                    'error': {'type': 'rate_limit_error', 'message': 'Test limit'},
                })
            return httpx.Response(200, json={
                'candidates': [{'content': {'role': 'model', 'parts': [
                    {'text': json.dumps(self.fields)},
                ]}, 'finishReason': 'STOP'}],
            })

        self.fields = {
            key: False if spec['type'] == 'boolean' else 'Test'
            for key, spec in assistant._intake_schema()['properties'].items()
        }
        self.fields['condition'] = assistant._intake_schema()['properties']['condition']['enum'][0]

        def factory(**kwargs):
            options = kwargs['http_options']
            self.assertEqual(options.retry_options.attempts, 3)
            # Keep the real retry policy, shortening only its waits for tests.
            options.retry_options.initial_delay = 0.001
            options.retry_options.max_delay = 0.002
            options.retry_options.jitter = 0.001
            options.base_url = 'https://gemini.test'
            options.httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            client = real_client(**kwargs)
            self.clients.append(client)
            return client

        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.factory = patch.object(assistant.genai, 'Client', side_effect=factory)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        snapshot = patch.object(assistant, 'snapshot', return_value={'status': 'stopped'})
        snapshot.start()
        self.addCleanup(snapshot.stop)

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aio.aclose()
            client.close()

    def configure(self, **environment):
        os.environ.update(environment)
        rotation = patch.object(assistant, '_client_rotation', assistant._ClientRotation())
        rotation.start()
        self.addCleanup(rotation.stop)

    async def test_order_wraparound_and_shared_chat_intake_sequence(self):
        self.configure(GEMINI_API_KEYS=' test-one, ,test-two,test-three, ',
                       GEMINI_API_KEY='test-unused')
        history = [{'role': 'user', 'content': 'Earlier question'},
                   {'role': 'assistant', 'content': 'Earlier answer'}]
        await assistant.ask('Now?', history)
        self.assertEqual(await assistant._extract('Test emergency'), self.fields)
        await assistant.ask('Next?')
        await assistant.ask('Wrap?')
        self.assertEqual([key for key, _ in self.requests],
                         ['test-one', 'test-two', 'test-three', 'test-one'])
        self.assertEqual(len(self.clients), 3)
        chat, intake = self.requests[0][1], self.requests[1][1]
        self.assertEqual(chat['contents'][:2], [
            {'role': 'user', 'parts': [{'text': 'Earlier question'}]},
            {'role': 'model', 'parts': [{'text': 'Earlier answer'}]},
        ])
        config = chat['generationConfig']
        self.assertEqual(config['thinkingConfig']['thinking_level'], assistant.EFFORT.upper())
        self.assertEqual(config['maxOutputTokens'], 4000)
        self.assertEqual(intake['generationConfig']['maxOutputTokens'], 2000)
        self.assertEqual(intake['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(intake['generationConfig']['responseJsonSchema'], assistant._intake_schema())

    async def test_single_key_fallback(self):
        for keys in ('', ' , , '):
            with self.subTest(keys=keys):
                self.configure(GEMINI_API_KEYS=keys, GEMINI_API_KEY=' test-single ')
                for _ in range(4):
                    await assistant.ask('Test')
        self.assertEqual([key for key, _ in self.requests], ['test-single'] * 8)
        self.assertEqual(len(self.clients), 2)

    async def test_missing_list_uses_single_key(self):
        self.configure(GEMINI_API_KEY='test-single')
        await assistant.ask('Test')
        await assistant.ask('Again')
        self.assertEqual([key for key, _ in self.requests], ['test-single'] * 2)
        self.assertEqual(len(self.clients), 1)

    async def test_concurrent_requests_keep_their_credentials(self):
        self.configure(GEMINI_API_KEYS='test-one,test-two,test-three')
        await asyncio.gather(*(assistant.ask(str(i)) for i in range(30)))
        self.assertEqual(Counter(key for key, _ in self.requests),
                         {'test-one': 10, 'test-two': 10, 'test-three': 10})
        for key, body in self.requests:
            number = int(body['contents'][-1]['parts'][0]['text'].split('Question: ')[1])
            self.assertEqual(key, ['test-one', 'test-two', 'test-three'][number % 3])
        self.assertEqual([client._api_client.api_key for client in self.clients],
                         ['test-one', 'test-two', 'test-three'])

    async def test_rate_limit_retries_are_bounded_and_keep_same_key(self):
        self.configure(GEMINI_API_KEYS='test-one,test-two')
        self.rate_limited = True
        with self.assertRaisesRegex(assistant.AssistantUnavailable, 'Too many requests'):
            await assistant.ask('Test')
        self.assertEqual([key for key, _ in self.requests], ['test-one'] * 3)
        self.rate_limited = False
        await assistant.ask('New request')
        self.assertEqual(self.requests[-1][0], 'test-two')

    async def test_missing_keys_are_reported_without_a_request(self):
        self.configure()
        with patch.object(assistant.genai, 'Client') as factory:
            with self.assertRaisesRegex(assistant.AssistantUnavailable, 'GEMINI_API_KEYS'):
                await assistant.ask('Test')
            factory.assert_not_called()

    async def test_intake_rejects_incomplete_json(self):
        self.configure(GEMINI_API_KEY='test-single')
        self.fields = {'summary': 'Missing required fields'}
        with self.assertRaises(assistant.IntakeError):
            await assistant._extract('Test')

    async def test_thread_safe_selection_initializes_each_client_once(self):
        self.configure(GEMINI_API_KEYS='test-one,test-two,test-three')
        with patch.object(assistant.genai, 'Client',
                          side_effect=lambda **kw: SimpleNamespace(**kw)) as factory:
            with ThreadPoolExecutor(max_workers=12) as executor:
                clients = list(executor.map(lambda _: assistant._get_client(), range(300)))
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(Counter(client.api_key for client in clients),
                         {'test-one': 100, 'test-two': 100, 'test-three': 100})


if __name__ == '__main__':
    unittest.main()
