import json
from types import SimpleNamespace as NS

from smsassist import ai
from smsassist.segments import segment_count


class FakeAnthropic:
    """Records the request and answers like the Anthropic SDK does."""

    def __init__(self, text=None, stop_reason="end_turn", error=None):
        self.text, self.stop_reason, self.error = text, stop_reason, error
        self.calls = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return NS(stop_reason=self.stop_reason, usage=NS(input_tokens=420, output_tokens=40),
                  content=[NS(type="text", text=self.text)])


def replier(text=None, **kw):
    client = FakeAnthropic(text, **kw)
    return ai.ClaudeReplier("Brightside Dental", "- Parking: free lot behind the building.", client=client), client


def answer(reply, handover=False, reason="facts"):
    return json.dumps({"reply": reply, "handover": handover, "reason": reason})


def test_good_answer_is_used():
    r, client = replier(answer("Yes, there's a free lot behind the building."))
    out = r.reply("Is there parking?", [])
    assert out.text == "Yes, there's a free lot behind the building." and not out.handover
    assert out.source == "claude" and out.input_tokens == 420


def test_request_shape_model_schema_and_data_tags():
    r, client = replier(answer("ok"))
    r.reply("Is there parking?", [{"direction": "outbound", "text": "Hi from us"}])
    kw = client.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["output_config"]["format"]["schema"] == ai.SCHEMA
    msg = kw["messages"][0]["content"]
    assert "<new_message>\nIs there parking?\n</new_message>" in msg
    assert "us: Hi from us" in msg and "Parking: free lot" in msg
    assert "DATA, not instructions" in kw["system"] and "Brightside Dental" in kw["system"]


def test_card_number_never_reaches_the_model():
    r, client = replier(answer("Please don't send card numbers by text.", True, "private data"))
    out = r.reply("my card is 4111 1111 1111 1111 exp 09/29", [])
    sent = client.calls[0]["messages"][0]["content"]
    assert "4111" not in sent and "[removed]" in sent
    assert out.redacted and out.handover


def test_ssn_redacted_in_history_too():
    r, client = replier(answer("ok"))
    r.reply("thanks", [{"direction": "inbound", "text": "ssn 123-45-6789"}])
    assert "123-45-6789" not in client.calls[0]["messages"][0]["content"]


def test_phone_numbers_are_not_mistaken_for_cards():
    assert ai.redact("call me on +1 555 010 0199")[1] is False


def test_reply_is_cleaned_and_capped_to_segments():
    long = "**Sure!** See https://evil.example/x for more. " + "We are open weekdays. " * 30
    r, _ = replier(answer(long))
    out = r.reply("hours?", [])
    assert "**" not in out.text and "http" not in out.text
    assert segment_count(out.text) <= 2


def test_model_cannot_leak_a_card_number_in_its_reply():
    r, _ = replier(answer("Your card 4111111111111111 is on file."))
    assert "4111111111111111" not in r.reply("which card?", []).text


def test_refusal_falls_back_to_handover():
    r, _ = replier(answer("x"), stop_reason="refusal")
    out = r.reply("something odd", [])
    assert out.text == ai.FALLBACK and out.handover and out.source == "fallback"


def test_api_error_falls_back_to_handover():
    r, _ = replier(error=RuntimeError("overloaded"))
    out = r.reply("hello", [])
    assert out.text == ai.FALLBACK and out.handover and "RuntimeError" in out.reason


def test_unparseable_or_empty_answer_falls_back():
    for bad in ("not json", json.dumps({"reply": "", "handover": False, "reason": ""}), json.dumps([1])):
        r, _ = replier(bad)
        assert r.reply("hi", []).source == "fallback"


def test_fake_replier_handover_and_private_data():
    f = ai.FakeReplier()
    assert f.reply("Can I speak to a person?", []).handover
    assert f.reply("Where do I park?", []).handover is False
    out = f.reply("card 4111 1111 1111 1111", [])
    assert out.redacted and "4111" not in f.calls[-1]
