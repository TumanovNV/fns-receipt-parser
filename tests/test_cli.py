import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import export_nalog_receipts as exporter


def parse(argv):
    """Parse CLI arguments; SystemExit code is returned instead of raised."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        try:
            return exporter.parse_args(argv), stderr.getvalue()
        except SystemExit as e:
            return e.code, stderr.getvalue()


class NormalizeTokenTest(unittest.TestCase):
    def test_plain_token(self):
        self.assertEqual(exporter.normalize_token("abc.def"), "abc.def")

    def test_bearer_prefix_is_removed(self):
        self.assertEqual(exporter.normalize_token("Bearer abc.def"), "abc.def")
        self.assertEqual(exporter.normalize_token("bearer   abc.def"), "abc.def")

    def test_whitespace_is_stripped(self):
        self.assertEqual(exporter.normalize_token("  abc.def \n"), "abc.def")
        self.assertEqual(exporter.normalize_token("  Bearer abc.def  "), "abc.def")

    def test_value_without_prefix_is_kept_whole(self):
        self.assertEqual(exporter.normalize_token("abc def"), "abc def")

    def test_empty_values(self):
        self.assertEqual(exporter.normalize_token(""), "")
        self.assertEqual(exporter.normalize_token("   "), "")
        self.assertEqual(exporter.normalize_token(None), "")
        self.assertEqual(exporter.normalize_token("Bearer "), "")


class ReadTokenTest(unittest.TestCase):
    def test_environment_variable_has_priority(self):
        with mock.patch.object(exporter.getpass, "getpass") as prompt, \
                redirect_stdout(io.StringIO()):
            token = exporter.read_token({"FNS_TOKEN": " Bearer env-token "})
        self.assertEqual(token, "env-token")
        prompt.assert_not_called()

    def test_falls_back_to_hidden_prompt(self):
        with mock.patch.object(exporter.getpass, "getpass",
                               return_value="Bearer typed") as prompt, \
                redirect_stdout(io.StringIO()):
            token = exporter.read_token({})
        self.assertEqual(token, "typed")
        prompt.assert_called_once()

    def test_blank_environment_variable_falls_back_to_prompt(self):
        with mock.patch.object(exporter.getpass, "getpass",
                               return_value="typed") as prompt, \
                redirect_stdout(io.StringIO()):
            token = exporter.read_token({"FNS_TOKEN": "   "})
        self.assertEqual(token, "typed")
        prompt.assert_called_once()

    def test_token_is_not_printed(self):
        out = io.StringIO()
        with redirect_stdout(out):
            exporter.read_token({"FNS_TOKEN": "secret-value"})
        self.assertNotIn("secret-value", out.getvalue())


class ParseArgsTest(unittest.TestCase):
    def test_no_arguments_keeps_v01_defaults(self):
        args, _ = parse([])
        self.assertIsNone(args.date_from)
        self.assertIsNone(args.date_to)
        self.assertEqual(args.output_dir, Path("nalog_receipts_export"))
        self.assertEqual(args.request_delay, 0.08)

    def test_valid_dates(self):
        args, _ = parse(["--date-from", "2026-09-01", "--date-to", "2026-09-17"])
        self.assertEqual(args.date_from, "2026-09-01")
        self.assertEqual(args.date_to, "2026-09-17")

    def test_same_day_range_is_allowed(self):
        args, _ = parse(["--date-from", "2026-09-01", "--date-to", "2026-09-01"])
        self.assertEqual(args.date_from, args.date_to)

    def test_single_bound_is_allowed(self):
        args, _ = parse(["--date-from", "2026-09-01"])
        self.assertEqual(args.date_from, "2026-09-01")
        self.assertIsNone(args.date_to)

    def test_invalid_dates_are_rejected(self):
        for value in ["2026-13-01", "2026-02-30", "17.09.2026", "2026-9-1",
                      "20260901", "2026-09-01T00:00", ""]:
            with self.subTest(value=value):
                code, err = parse(["--date-from", value])
                self.assertEqual(code, 2)
                self.assertIn("некорректная дата", err)

    def test_date_from_after_date_to_is_rejected(self):
        code, err = parse(["--date-from", "2026-10-01", "--date-to", "2026-09-01"])
        self.assertEqual(code, 2)
        self.assertIn("не может быть позже", err)

    def test_date_order_error_happens_before_any_api_call(self):
        with mock.patch.object(exporter.urllib.request, "urlopen") as urlopen, \
                mock.patch.object(exporter.getpass, "getpass") as prompt, \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                exporter.main(["--date-from", "2026-10-01", "--date-to", "2026-09-01"])
        urlopen.assert_not_called()
        prompt.assert_not_called()

    def test_output_dir_relative_and_absolute(self):
        args, _ = parse(["--output-dir", "exports/2026"])
        self.assertEqual(args.output_dir, Path("exports/2026"))
        args, _ = parse(["--output-dir", "/tmp/fns"])
        self.assertEqual(args.output_dir, Path("/tmp/fns"))

    def test_output_dir_expands_home(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/tester"}):
            args, _ = parse(["--output-dir", "~/Downloads/fns-receipts"])
        self.assertEqual(args.output_dir, Path("/home/tester/Downloads/fns-receipts"))

    def test_request_delay_validation(self):
        args, _ = parse(["--request-delay", "0.5"])
        self.assertEqual(args.request_delay, 0.5)
        for value in ("-1", "61", "nan", "abc"):
            with self.subTest(value=value):
                code, _ = parse(["--request-delay", value])
                self.assertEqual(code, 2)

    def test_page_size_is_not_a_public_option(self):
        code, err = parse(["--page-size", "25"])
        self.assertEqual(code, 2)
        self.assertIn("--page-size", err)

    def test_help(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            exporter.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        for option in ("--date-from", "--date-to", "--output-dir",
                       "--request-delay", "FNS_TOKEN"):
            self.assertIn(option, out.getvalue())


if __name__ == "__main__":
    unittest.main()
