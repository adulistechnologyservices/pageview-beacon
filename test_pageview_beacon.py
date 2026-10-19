"""Run with:  python3 -m unittest -v

No AWS, no network, no test framework. ``parse_hit`` is pure; ``handler`` is
exercised with a stubbed table.
"""

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

import pageview_beacon as pb

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"


def event(body=None, method="POST", ua=UA, raw=None):
    return {
        "requestContext": {"http": {"method": method}},
        "headers": {"User-Agent": ua, "Content-Type": "text/plain"},
        "body": raw if raw is not None else json.dumps(body if body is not None else {}),
    }


def hit(**overrides):
    body = {
        "site_id": "rosas-tacos",
        "path": "index.html",
        "referrer": "https://example.com/",
        "screen_w": 390,
        "is_mobile": True,
    }
    body.update(overrides)
    return event(body)


class ParseHit(unittest.TestCase):
    def test_good_hit_becomes_an_item(self):
        status, item = pb.parse_hit(hit(), now=NOW)
        self.assertEqual(status, 200)
        self.assertEqual(item["site_id"], "rosas-tacos")
        self.assertEqual(item["path"], "index.html")
        self.assertEqual(item["referrer"], "https://example.com/")
        self.assertEqual(item["screen_w"], 390)
        self.assertIs(item["is_mobile"], True)
        self.assertTrue(item["seen_at"].startswith("2026-01-01T12:00:00+00:00#"))

    def test_ttl_is_retention_days_ahead(self):
        _, item = pb.parse_hit(hit(), now=NOW)
        expected = int(NOW.timestamp()) + pb.RETENTION_DAYS * 86400
        self.assertEqual(item["ttl"], expected)

    def test_two_hits_in_the_same_instant_get_different_sort_keys(self):
        _, a = pb.parse_hit(hit(), now=NOW)
        _, b = pb.parse_hit(hit(), now=NOW)
        self.assertNotEqual(a["seen_at"], b["seen_at"])

    def test_preflight_writes_nothing(self):
        status, item = pb.parse_hit(event(method="OPTIONS"))
        self.assertEqual((status, item), (200, None))

    def test_crawler_is_accepted_but_not_counted(self):
        for ua in ("Googlebot/2.1", "curl/8.4.0", "python-requests/2.31",
                   "HeadlessChrome/120", "Mozilla/5.0 (compatible; bingbot/2.0)"):
            with self.subTest(ua=ua):
                status, item = pb.parse_hit(event({"site_id": "rosas-tacos"}, ua=ua))
                self.assertEqual((status, item), (200, None))

    def test_text_plain_body_is_still_parsed_as_json(self):
        # The client sends text/plain on purpose; the content type is ignored.
        status, item = pb.parse_hit(hit(), now=NOW)
        self.assertEqual(status, 200)
        self.assertIsNotNone(item)

    def test_malformed_json_is_400(self):
        status, item = pb.parse_hit(event(raw="{not json"))
        self.assertEqual((status, item), (400, None))

    def test_non_object_json_is_400(self):
        for raw in ("[]", '"x"', "42", "null"):
            with self.subTest(raw=raw):
                status, item = pb.parse_hit(event(raw=raw))
                self.assertEqual((status, item), (400, None))

    def test_missing_site_id_is_400(self):
        status, item = pb.parse_hit(event({"path": "index.html"}))
        self.assertEqual((status, item), (400, None))

    def test_site_id_must_be_a_slug(self):
        for bad in ("../../etc", "rosas tacos", "Rosas_Tacos", "-leading", "a" * 66,
                    "<script>", "rosas/tacos"):
            with self.subTest(site_id=bad):
                status, item = pb.parse_hit(hit(site_id=bad))
                self.assertEqual((status, item), (400, None))

    def test_site_id_is_normalised(self):
        _, item = pb.parse_hit(hit(site_id="  ROSAS-TACOS "), now=NOW)
        self.assertEqual(item["site_id"], "rosas-tacos")

    def test_long_path_and_referrer_are_truncated(self):
        _, item = pb.parse_hit(hit(path="p" * 1000, referrer="r" * 1000), now=NOW)
        self.assertEqual(len(item["path"]), pb.MAX_PATH)
        self.assertEqual(len(item["referrer"]), pb.MAX_REFERRER)

    def test_missing_optional_fields_get_defaults(self):
        _, item = pb.parse_hit(event({"site_id": "rosas-tacos"}), now=NOW)
        self.assertEqual(item["path"], "/")
        self.assertEqual(item["referrer"], "")
        self.assertEqual(item["screen_w"], 0)
        self.assertIs(item["is_mobile"], False)

    def test_non_numeric_screen_width_does_not_crash(self):
        _, item = pb.parse_hit(hit(screen_w="wide"), now=NOW)
        self.assertEqual(item["screen_w"], 0)

    def test_nothing_personal_is_stored(self):
        _, item = pb.parse_hit(hit(email="x@y.z", ip="1.2.3.4", user_id="u1"), now=NOW)
        self.assertEqual(
            set(item), {"site_id", "seen_at", "path", "referrer", "screen_w", "is_mobile", "ttl"}
        )


class Handler(unittest.TestCase):
    def setUp(self):
        self.put = mock.Mock()
        patcher = mock.patch.object(pb, "table", return_value=mock.Mock(put_item=self.put))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_good_hit_is_written_and_counted(self):
        res = pb.handler(hit(), None)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(json.loads(res["body"]), {"ok": True, "counted": True})
        self.put.assert_called_once()
        self.assertEqual(self.put.call_args.kwargs["Item"]["site_id"], "rosas-tacos")

    def test_crawler_gets_200_and_no_write(self):
        res = pb.handler(event({"site_id": "rosas-tacos"}, ua="Googlebot/2.1"), None)
        self.assertEqual(json.loads(res["body"]), {"ok": True, "counted": False})
        self.put.assert_not_called()

    def test_bad_request_gets_400_and_no_write(self):
        res = pb.handler(hit(site_id="../x"), None)
        self.assertEqual(res["statusCode"], 400)
        self.put.assert_not_called()

    def test_preflight_carries_cors_headers(self):
        res = pb.handler(event(method="OPTIONS"), None)
        self.assertEqual(res["statusCode"], 200)
        self.assertIn("Access-Control-Allow-Origin", res["headers"])
        self.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
