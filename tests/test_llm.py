import httpx
import pytest

from astro_app.auth import oauth
from astro_app.auth import config as C
from astro_app.auth.session import AuthSession
from astro_app.auth.store import CredentialStore
from astro_app.llm import (AuthRejected, FakeLLMClient, IncompleteResponse, InvalidOutput, LLMRequest, Model,
                           PlanNotEligible, SIWCClient, TemporarilyUnavailable, UnsupportedRequest,
                           UsageLimitExceeded)
from astro_app.llm.siwc import FORBIDDEN_FIELDS, build_body
from fake_openai import FakeOpenAI, sse, sse_failed, sse_text

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def env(tmp_path):
    fake = FakeOpenAI()
    store = CredentialStore(tmp_path / "creds")
    pending, url = oauth.start(C.redirect_uri(1455), "urn:uuid:3f0e3f8a-2b3c-4d5e-8f60-718293a4b5c6")
    async with fake.client() as http:
        await oauth.complete(pending, fake.authorize(url), http, store, oauth.IdTokenVerifier(http))
        sleeps = []

        async def sleep(s):
            sleeps.append(s)

        client = SIWCClient(AuthSession(store, http), http, sleep=sleep)
        yield fake, client, store, sleeps


REQ = LLMRequest(instructions="Be brief.", input="Say hi", purpose="test")


async def test_models_filtered_to_listed(env):
    fake, client, _, _ = env
    assert await client.list_models() == [Model("gpt-test-large", "GPT Test Large")]


async def test_streams_text_and_picks_first_listed_model(env):
    fake, client, _, _ = env
    fake.responses = [sse_text("Hello, world!")]
    r = await client.complete(REQ)
    assert r.text == "Hello, world!" and r.request_id == "req_test" and r.usage["output_tokens"] == 13
    body = fake.response_bodies[0]
    assert body["model"] == "gpt-test-large" and body["store"] is False and body["stream"] is True
    assert body["instructions"] == "Be brief." and body["input"] == [{"role": "user", "content": "Say hi"}]
    assert not FORBIDDEN_FIELDS & body.keys()


async def test_structured_output(env):
    fake, client, _, _ = env
    fake.responses = [sse_text('{"paragraphs": ["a", "b"]}')]
    schema = {"type": "object", "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
              "required": ["paragraphs"], "additionalProperties": False}
    r = await client.complete(LLMRequest("x", "y", schema=schema, schema_name="section"))
    assert r.data == {"paragraphs": ["a", "b"]}
    fmt = fake.response_bodies[0]["text"]["format"]
    assert fmt == {"type": "json_schema", "name": "section", "schema": schema, "strict": True}


async def test_invalid_json_is_retried_then_fails(env):
    fake, client, _, sleeps = env
    fake.responses = [sse_text("not json")] * 3
    with pytest.raises(InvalidOutput):
        await client.complete(LLMRequest("x", "y", schema={"type": "object"}))
    assert len(fake.response_bodies) == 3 and sleeps == [2, 4]


async def test_usage_limit_mid_stream_stops_without_retry(env):
    fake, client, _, sleeps = env
    fake.responses = [sse_failed("subscription_sharing_usage_limit_exceeded")]
    with pytest.raises(UsageLimitExceeded) as e:
        await client.complete(REQ)
    assert e.value.request_id == "req_test" and len(fake.response_bodies) == 1 and sleeps == []


async def test_unavailable_is_retried_with_backoff(env):
    fake, client, _, sleeps = env
    fake.responses = [httpx.Response(503, json={"detail": "Direct routing unavailable"}),
                      sse_failed("subscription_sharing_usage_unavailable"), sse_text("done")]
    assert (await client.complete(REQ)).text == "done"
    assert sleeps == [2, 4]


async def test_unavailable_gives_up_after_bounded_attempts(env):
    fake, client, _, _ = env
    fake.responses = [httpx.Response(503, json={"detail": "x"})] * 3
    with pytest.raises(TemporarilyUnavailable):
        await client.complete(REQ)
    assert len(fake.response_bodies) == 3


async def test_401_refreshes_once_then_succeeds(env):
    fake, client, store, _ = env
    old = store.active().access_token
    fake.access_tokens.discard(old)  # server no longer accepts it
    fake.responses = [sse_text("after refresh")]
    assert (await client.complete(REQ)).text == "after refresh"
    assert store.active().access_token != old


async def test_401_twice_is_auth_rejected(env):
    fake, client, store, _ = env
    fake.responses = [httpx.Response(401, json={"detail": "nope"}), httpx.Response(401, json={"detail": "nope"})]
    with pytest.raises(AuthRejected):
        await client.complete(REQ)


async def test_unsupported_capability_is_not_retried(env):
    fake, client, _, sleeps = env
    fake.responses = [httpx.Response(400, json={"error": {"code": "subscription_sharing_unsupported_capability",
                                                          "param": "tools[0]", "message": "no"}})]
    with pytest.raises(UnsupportedRequest) as e:
        await client.complete(REQ)
    assert e.value.param == "tools[0]" and sleeps == []


async def test_not_eligible(env):
    fake, client, _, _ = env
    fake.responses = [httpx.Response(403, json={"error": {"code": "subscription_sharing_user_not_eligible"}})]
    with pytest.raises(PlanNotEligible):
        await client.complete(REQ)


async def test_stream_without_completed_is_incomplete(env):
    fake, client, _, _ = env
    cut = sse({"type": "response.created", "response": {}}, {"type": "response.output_text.delta", "delta": "half"})
    fake.responses = [cut] * 3
    with pytest.raises(IncompleteResponse):
        await client.complete(REQ)


def test_body_guard_rejects_system_role():
    with pytest.raises(UnsupportedRequest):
        build_body(LLMRequest("x", [{"role": "system", "content": "no"}]), "m")


async def test_fake_client_records_and_replays():
    f = FakeLLMClient([{"a": 1}, "plain", UsageLimitExceeded("stop")])
    assert (await f.complete(LLMRequest("i", "x", schema={}))).data == {"a": 1}
    assert (await f.complete(LLMRequest("i", "x"))).text == "plain"
    with pytest.raises(UsageLimitExceeded):
        await f.complete(LLMRequest("i", "x"))
    assert len(f.requests) == 3
