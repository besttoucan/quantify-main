"""What an operator is told when the register will not sync.

The rule these all test: the sentence on screen is written for somebody standing
in a kitchen, the provider's own wording never reaches them, and Quantify only
claims to know what went wrong when it actually does.
"""

from __future__ import annotations

import json
import unittest

from quantify_app.connectors import ConnectorError, _classify_http

TOKEN_GONE = json.dumps({"errors": [{"category": "AUTHENTICATION_ERROR", "code": "UNAUTHORIZED",
                                     "detail": "This request could not be authorized."}]})


class Headers(dict):
    """urllib hands back a mapping with .get, which is all the classifier uses."""


class WhatTheOperatorIsTold(unittest.TestCase):
    def test_no_provider_wording_ever_reaches_the_message(self) -> None:
        error = _classify_http(401, TOKEN_GONE)
        # The raw body is kept, because a support email needs it.
        self.assertIn("UNAUTHORIZED", error.detail)
        self.assertEqual(error.provider_code, "UNAUTHORIZED")
        # It is not in the sentence a person reads.
        for leak in ("UNAUTHORIZED", "AUTHENTICATION_ERROR", "{", "HTTP", "401"):
            self.assertNotIn(leak, error.message, f"{leak} leaked into the operator message")

    def test_every_message_is_a_plain_sentence(self) -> None:
        for code in (401, 403, 404, 429, 500, 503, 418):
            with self.subTest(code=code):
                message = _classify_http(code, "{}").message
                self.assertTrue(message.endswith("."), f"{code} does not end in a full stop")
                self.assertNotIn("—", message)
                self.assertGreater(len(message.split()), 6, f"{code} is too terse to act on")
                for word in ("leverage", "seamless", "unlock", "effortless"):
                    self.assertNotIn(word, message.lower())

    def test_a_rejected_token_says_to_reconnect_and_where(self) -> None:
        error = _classify_http(401, TOKEN_GONE)
        self.assertEqual(error.reason, "token_rejected")
        self.assertTrue(error.explained)
        self.assertIn("Settings", error.message)

    def test_rate_limiting_reassures_that_nothing_is_broken(self) -> None:
        error = _classify_http(429, "{}", Headers({"Retry-After": "45"}))
        self.assertEqual(error.reason, "rate_limited")
        self.assertEqual(error.retry_after, 45)
        self.assertIn("45 seconds", error.message)
        # This one is not the operator's fault and the copy has to say so,
        # otherwise they go and reconnect a connection that is fine.
        self.assertIn("Nothing is wrong with the connection", error.message)

    def test_a_missing_retry_after_still_gives_advice(self) -> None:
        for headers in (None, Headers({}), Headers({"Retry-After": "soon"})):
            with self.subTest(headers=headers):
                error = _classify_http(429, "{}", headers)
                self.assertIsNone(error.retry_after)
                self.assertIn("few minutes", error.message)

    def test_a_provider_outage_does_not_send_anybody_to_a_setting(self) -> None:
        error = _classify_http(503, "{}")
        self.assertEqual(error.reason, "provider_down")
        self.assertIn("their end", error.message)
        self.assertNotIn("Reconnect", error.message)


class WhenQuantifyDoesNotKnow(unittest.TestCase):
    """explained is the flag that decides whether support is worth offering."""

    def test_an_unrecognised_failure_admits_it(self) -> None:
        error = _classify_http(418, "something nobody has seen before")
        self.assertEqual(error.reason, "unknown")
        self.assertFalse(error.explained)
        self.assertIn("cannot tell why", error.message)
        # Saying nothing changed is the part that stops somebody re-running it
        # in a panic, so it has to be there.
        self.assertIn("Nothing has changed", error.message)

    def test_everything_recognised_claims_to_be_explained(self) -> None:
        for code in (401, 403, 404, 429, 500):
            with self.subTest(code=code):
                self.assertTrue(_classify_http(code, "{}").explained)

    def test_unparseable_json_never_raises_out_of_the_classifier(self) -> None:
        # The classifier runs while handling a failure. If it throws, the real
        # failure is replaced by a stack trace and the operator learns nothing.
        for body in ("", "not json", "[]", "null", '{"errors": "wrong shape"}', '{"errors": [3]}'):
            with self.subTest(body=body):
                error = _classify_http(401, body)
                self.assertIsInstance(error, ConnectorError)
                self.assertEqual(error.provider_code, "")

    def test_detail_is_capped_so_a_huge_body_cannot_fill_the_log(self) -> None:
        self.assertLessEqual(len(_classify_http(500, "x" * 9000).detail), 1200)


if __name__ == "__main__":
    unittest.main()
