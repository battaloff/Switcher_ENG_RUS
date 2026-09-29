import json

import anthropic
import httpx2
import pytest

from switcher.ai import AIError, Assistant, apply_review, build_digest
from switcher.config import AI
from switcher.layouts import EN, RU


def fake_client(reply: dict | str, captured: list, stop_reason: str = "end_turn"):
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append({"body": json.loads(request.content), "headers": dict(request.headers)})
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    return anthropic.Anthropic(api_key="test", base_url="https://api.test",
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


PIECES = [
    {"screen": "z", "en": "z", "ru": "я", "delim": " "},
    {"screen": "pfgeibk", "en": "pfgeibk", "ru": "запушил", "delim": " "},
    {"screen": "main", "en": "main", "ru": "ьфшт", "delim": ""},
]


def test_fix_phrase_request_and_reassembly():
    captured = []
    assistant = Assistant(AI(), client=fake_client({"words": ["я", "запушил", "main"]}, captured))
    assert assistant.fix_phrase(PIECES, style="пишет строчными", app="Telegram") == "я запушил main"
    body = captured[0]["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured[0]["headers"]["anthropic-beta"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "low"
    assert "пишет строчными" in body["system"]
    sent = json.loads(body["messages"][0]["content"])
    assert sent["words"][1] == {"screen": "pfgeibk", "en": "pfgeibk", "ru": "запушил"}
    assert "delim" not in sent["words"][1]


def test_fix_phrase_rejects_misaligned_answers():
    assistant = Assistant(AI(), client=fake_client({"words": ["я запушил main"]}, []))
    with pytest.raises(AIError):
        assistant.fix_phrase(PIECES)


def test_refusal_is_reported():
    assistant = Assistant(AI(), client=fake_client("", [], stop_reason="refusal"))
    with pytest.raises(AIError, match="отказался"):
        assistant.fix_phrase(PIECES)


def test_no_fallbacks_for_models_without_them():
    captured = []
    assistant = Assistant(AI(model="claude-haiku-4-5"), client=fake_client({"words": ["a", "b", "c"]}, captured))
    assistant.fix_phrase(PIECES)
    assert "fallbacks" not in captured[0]["body"]


def test_review_digest_and_validation(profile, keyboard):
    profile.count_word("Telegram", RU)
    profile.log_event("undo", app="Telegram", keys="ok", typed_lang=EN, final_lang=EN, typed_text="ok",
                      final_text="ok", detail={"was": "щл", "via": "hotkey"})
    profile.add_rule("layout", "ghbdtn", EN, source="learned")
    profile.add_rule("layout", "vs", EN, source="user")
    proposal = {
        "layout_rules": [
            {"word": "коммит", "lang": "ru", "app": "", "reason": "жаргон"},
            {"word": "Vue.", "lang": "en", "app": "Telegram", "reason": "фреймворк"},
            {"word": "mixed слово", "lang": "en", "app": "", "reason": "bad"},
            {"word": "привет", "lang": "en", "app": "", "reason": "wrong script"},
        ],
        "replace_rules": [
            {"wrong": "превет", "right": "привет", "reason": "частая опечатка"},
            {"wrong": "кот", "right": "собака", "reason": "not a typo"},
        ],
        "drop_rules": [
            {"kind": "layout", "word": "ghbdtn", "reason": "устарело"},
            {"kind": "layout", "word": "vs", "reason": "user rules are never dropped"},
        ],
        "app_preferences": [
            {"app": "Telegram", "lang": "ru", "threshold_delta": 9, "reason": "чат"},
            {"app": "Unknown", "lang": "en", "threshold_delta": 0.2, "reason": "not seen"},
        ],
        "style_summary": "Пишете в основном по-русски.",
    }
    out = apply_review(proposal, profile, keyboard)
    assert profile.layout_rule("rjvvbn", "") == (RU, "ai")
    assert profile.layout_rule("vue", "Telegram") == (EN, "ai")
    assert profile.replace_rule("превет", "").value == "привет"
    assert profile.replace_rule("кот", "") is None
    assert profile.layout_rule("ghbdtn", "") is None
    assert profile.layout_rule("vs", "") == (EN, "user")
    assert profile.tuning()["Telegram"] == (0.5, RU)
    assert "Unknown" not in profile.tuning()
    assert profile.get_meta("style_summary") == "Пишете в основном по-русски."
    assert len(out.rejected) == 3

    digest = build_digest(profile, keyboard)
    assert digest["journal"][0]["switcher_made"] == "щл"
    assert {"kind": "layout", "word": "мы", "result": "en", "app": "", "source": "user", "hits": 1,
            "misses": 0} not in digest["rules"]
    assert any(r["word"] == "vs" and r["source"] == "user" for r in digest["rules"])
    assert digest["previous_style_summary"] == "Пишете в основном по-русски."


def test_review_request(profile, keyboard):
    captured = []
    reply = {"layout_rules": [], "replace_rules": [], "drop_rules": [], "app_preferences": [],
             "style_summary": "ok"}
    assistant = Assistant(AI(), client=fake_client(reply, captured))
    digest, proposal = assistant.review(profile, keyboard)
    assert proposal == reply
    assert captured[0]["body"]["output_config"]["effort"] == "medium"
    assert json.loads(captured[0]["body"]["messages"][0]["content"]) == digest
